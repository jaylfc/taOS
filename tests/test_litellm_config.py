"""Unit tests for tinyagentos/litellm_config.py.

Tests config generation and master-key loading in isolation: no real LiteLLM
process, no network calls, no live hardware reads.
"""
from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest

import tinyagentos.litellm_config as cfg_mod
from tinyagentos.litellm_config import (
    get_litellm_master_key,
    generate_litellm_config,
    _is_embedding_model,
    _local_backend_models_from_registry,
    _discover_ollama_models,
    _discover_ollama_backends_concurrent,
    EMBEDDING_ALIAS,
)


@pytest.fixture(autouse=True)
def clear_key_cache():
    cfg_mod._master_key_cache.clear()
    yield
    cfg_mod._master_key_cache.clear()


# ---------------------------------------------------------------------------
# get_litellm_master_key
# ---------------------------------------------------------------------------

class TestGetLiteLLMMasterKey:

    def test_returns_key_with_prefix(self, tmp_path):
        key = get_litellm_master_key(tmp_path)
        assert key.startswith("sk-taos-")

    def test_persists_key_to_disk(self, tmp_path):
        key = get_litellm_master_key(tmp_path)
        key_file = tmp_path / ".litellm_master_key"
        assert key_file.exists()
        assert key_file.read_text().strip() == key

    def test_key_file_mode_0600(self, tmp_path):
        get_litellm_master_key(tmp_path)
        key_file = tmp_path / ".litellm_master_key"
        mode = stat.S_IMODE(key_file.stat().st_mode)
        assert mode == 0o600

    def test_returns_cached_key_on_repeated_call(self, tmp_path):
        k1 = get_litellm_master_key(tmp_path)
        k2 = get_litellm_master_key(tmp_path)
        assert k1 is k2

    def test_reads_existing_key_from_disk(self, tmp_path):
        key_path = tmp_path / ".litellm_master_key"
        key_path.write_text("sk-taos-persisted\n")
        key = get_litellm_master_key(tmp_path)
        assert key == "sk-taos-persisted"

    def test_different_dirs_independent_keys(self, tmp_path):
        da = tmp_path / "a"
        db = tmp_path / "b"
        da.mkdir()
        db.mkdir()
        ka = get_litellm_master_key(da)
        kb = get_litellm_master_key(db)
        assert ka != kb

    def test_none_data_dir_returns_in_memory_key(self):
        key = get_litellm_master_key(None)
        assert key.startswith("sk-taos-")

    def test_none_data_dir_uses_separate_cache(self):
        k1 = get_litellm_master_key(None)
        k2 = get_litellm_master_key(None)
        assert k1 == k2

    def test_none_dir_and_path_dir_cached_independently(self, tmp_path):
        k_mem = get_litellm_master_key(None)
        k_disk = get_litellm_master_key(tmp_path)
        assert k_mem != k_disk

    def test_file_exists_error_branch_reads_winner_key(self, tmp_path, monkeypatch):
        winner = "sk-taos-winner"
        (tmp_path / ".litellm_master_key").write_text(winner)

        real_open = os.open

        def _fake_open(path, flags, mode=0o666):
            if "litellm_master_key" in str(path) and (flags & os.O_EXCL):
                raise FileExistsError("simulated race")
            return real_open(path, flags, mode)

        monkeypatch.setattr(os, "open", _fake_open)
        loaded = get_litellm_master_key(tmp_path)
        assert loaded == winner

    def test_empty_existing_file_raises_runtime_error(self, tmp_path, monkeypatch):
        key_path = tmp_path / ".litellm_master_key"
        key_path.write_text("   \n")

        real_open = os.open

        def _fake_open(path, flags, mode=0o666):
            if "litellm_master_key" in str(path) and (flags & os.O_EXCL):
                raise FileExistsError("simulated race")
            return real_open(path, flags, mode)

        monkeypatch.setattr(os, "open", _fake_open)
        with pytest.raises(RuntimeError, match="empty"):
            get_litellm_master_key(tmp_path)

    def test_creates_parent_dirs_if_missing(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c"
        key = get_litellm_master_key(deep)
        assert key.startswith("sk-taos-")
        assert (deep / ".litellm_master_key").exists()


# ---------------------------------------------------------------------------
# _is_embedding_model
# ---------------------------------------------------------------------------

class TestIsEmbeddingModel:

    @pytest.mark.parametrize(
        "name",
        [
            "nomic-embed-text",
            "qwen3-embedding-0.6b",
            "mxbai-embed-large",
            "text-embedding-ada-002",
            "text-embedding-3-small",
        ],
    )
    def test_embed_in_name(self, name):
        assert _is_embedding_model(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "bge-small-en-v1.5",
            "bge-m3",
            "gte-large",
            "gte-qwen2-7b-instruct",
            "e5-large-v2",
            "e5-mistral-7b-instruct",
            "arctic-embed-l",
            "snowflake-arctic-embed-m",
        ],
    )
    def test_known_embedding_prefixes(self, name):
        assert _is_embedding_model(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "llama-3.1-8b",
            "qwen2.5-7b-instruct",
            "gemma-2-9b",
            "mistral-7b",
        ],
    )
    def test_chat_models_not_embedding(self, name):
        assert _is_embedding_model(name) is False

    @pytest.mark.parametrize(
        "name",
        [
            "jina-reranker-v2",
            "bge-reranker-large",
            "cohere-rerank-v3",
        ],
    )
    def test_rerankers_not_embedding(self, name):
        assert _is_embedding_model(name) is False

    def test_case_insensitive(self):
        assert _is_embedding_model("Nomic-Embed-Text") is True
        assert _is_embedding_model("BGE-Small") is True


# ---------------------------------------------------------------------------
# generate_litellm_config -- structure
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMStructure:

    def test_returns_required_top_level_keys(self):
        config = generate_litellm_config([])
        assert set(config.keys()) == {
            "model_list",
            "router_settings",
            "general_settings",
            "litellm_settings",
        }

    def test_router_settings_defaults(self):
        config = generate_litellm_config([])
        rs = config["router_settings"]
        assert rs["routing_strategy"] == "simple-shuffle"
        assert rs["num_retries"] == 2
        assert rs["timeout"] == 120
        assert rs["enable_pre_call_checks"] is False

    def test_general_settings_disable_spend_logs(self):
        config = generate_litellm_config([])
        gs = config["general_settings"]
        assert gs["background_health_checks"] is False
        assert gs["disable_spend_logs"] is True

    def test_master_key_in_general_settings(self):
        config = generate_litellm_config([], master_key="sk-taos-test")
        assert config["general_settings"]["master_key"] == "sk-taos-test"

    def test_master_key_auto_generated_when_not_supplied(self):
        config = generate_litellm_config([])
        mk = config["general_settings"]["master_key"]
        assert mk.startswith("sk-taos-")

    def test_litellm_settings_callbacks(self):
        config = generate_litellm_config([])
        assert (
            config["litellm_settings"]["callbacks"]
            == "taos_callback.proxy_handler_instance"
        )

    def test_empty_backends_empty_model_list(self):
        config = generate_litellm_config([])
        assert config["model_list"] == []


# ---------------------------------------------------------------------------
# generate_litellm_config -- single ollama backend
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMOllamaBackend:

    def test_single_backend_produces_default_entry(self):
        backends = [
            {"name": "ollama-local", "type": "ollama", "url": "http://localhost:11434"}
        ]
        config = generate_litellm_config(backends)
        ml = config["model_list"]
        assert len(ml) == 1
        entry = ml[0]
        assert entry["model_name"] == "default"
        assert entry["litellm_params"]["model"] == "ollama_chat/default"
        assert entry["litellm_params"]["api_base"] == "http://localhost:11434"
        assert entry["metadata"]["backend_name"] == "ollama-local"

    def test_backend_model_overrides_default(self):
        backends = [
            {
                "name": "ollama-local",
                "type": "ollama",
                "url": "http://localhost:11434",
                "model": "llama3",
            }
        ]
        config = generate_litellm_config(backends)
        entry = config["model_list"][0]
        assert entry["litellm_params"]["model"] == "ollama_chat/llama3"

    def test_url_trailing_slash_stripped(self):
        backends = [
            {"name": "o", "type": "ollama", "url": "http://host:11434/"}
        ]
        config = generate_litellm_config(backends)
        entry = config["model_list"][0]
        assert entry["litellm_params"]["api_base"] == "http://host:11434"

    def test_priority_passed_to_metadata(self):
        backends = [
            {"name": "o", "type": "ollama", "url": "http://h:11434", "priority": 5}
        ]
        config = generate_litellm_config(backends)
        assert config["model_list"][0]["metadata"]["priority"] == 5

    def test_default_priority_is_99(self):
        backends = [
            {"name": "o", "type": "ollama", "url": "http://h:11434"}
        ]
        config = generate_litellm_config(backends)
        assert config["model_list"][0]["metadata"]["priority"] == 99


# ---------------------------------------------------------------------------
# generate_litellm_config -- rkllama backend
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMRkllamaBackend:

    def test_rkllama_uses_ollama_chat_prefix(self):
        backends = [
            {"name": "rk-box", "type": "rkllama", "url": "http://192.168.1.50:8080"}
        ]
        config = generate_litellm_config(backends)
        entry = config["model_list"][0]
        assert entry["litellm_params"]["model"] == "ollama_chat/default"
        assert entry["litellm_params"]["api_base"] == "http://192.168.1.50:8080"


# ---------------------------------------------------------------------------
# generate_litellm_config -- hailo-ollama backend (S5)
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMHailoOllamaBackend:

    def test_hailo_ollama_uses_ollama_chat_prefix(self):
        """hailo-ollama is Ollama-compatible, so the default chat entry must use
        the ollama_chat prefix and point at the remapped port 7836."""
        backends = [
            {"name": "hailo-box", "type": "hailo-ollama", "url": "http://localhost:7836"}
        ]
        config = generate_litellm_config(backends)
        entry = config["model_list"][0]
        assert entry["litellm_params"]["model"] == "ollama_chat/default"
        assert entry["litellm_params"]["api_base"] == "http://localhost:7836"

    def test_hailo_ollama_discovered_embedding_emits_ollama_prefix(self):
        """Registered models keep the ollama/<name> LiteLLM prefix, identical to
        rkllama (S5)."""
        backends = [
            {"name": "hailo-box", "type": "hailo-ollama", "url": "http://h:7836"}
        ]
        discovered = {"http://h:7836": ["nomic-embed-text"]}
        config = generate_litellm_config(backends, discovered=discovered)
        ml = config["model_list"]
        embed_entries = [e for e in ml if e.get("model_info", {}).get("mode") == "embedding"]
        assert len(embed_entries) >= 1
        assert embed_entries[0]["litellm_params"]["model"] == "ollama/nomic-embed-text"


# ---------------------------------------------------------------------------
# generate_litellm_config -- cloud backends
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMCloudBackend:

    def test_openai_backend_with_declared_models(self):
        backends = [
            {
                "name": "openai",
                "type": "openai",
                "models": ["gpt-4o", "gpt-4o-mini"],
                "api_key": "sk-real-key",
            }
        ]
        config = generate_litellm_config(backends)
        ml = config["model_list"]
        # Two declared-model entries + one "default" entry
        assert len(ml) == 3
        # Declared models come first (cloud loop appends before default)
        assert ml[0]["model_name"] == "gpt-4o"
        assert ml[0]["litellm_params"]["model"] == "openai/gpt-4o"
        assert ml[0]["litellm_params"]["api_key"] == "sk-real-key"
        assert ml[1]["model_name"] == "gpt-4o-mini"
        assert ml[1]["litellm_params"]["model"] == "openai/gpt-4o-mini"
        # Default entry is last
        assert ml[2]["model_name"] == "default"
        assert ml[2]["litellm_params"]["model"] == "openai/default"

    def test_cloud_backend_with_dict_models(self):
        backends = [
            {
                "name": "openai",
                "type": "openai",
                "models": [{"id": "gpt-4o", "name": "GPT-4o"}],
                "api_key": "sk-key",
            }
        ]
        config = generate_litellm_config(backends)
        assert config["model_list"][0]["model_name"] == "gpt-4o"

    def test_cloud_backend_with_api_key_secret(self):
        backends = [
            {
                "name": "openai",
                "type": "openai",
                "models": ["gpt-4o"],
                "api_key_secret": "OPENAI_API_KEY",
            }
        ]
        config = generate_litellm_config(backends)
        entry = config["model_list"][0]
        assert entry["litellm_params"]["api_key"] == "os.environ/OPENAI_API_KEY"

    def test_cloud_backend_missing_url_or_models_logs_warning(self, caplog):
        backends = [
            {"name": "bad-cloud", "type": "openai"}
        ]
        config = generate_litellm_config(backends)
        # Should still produce a default entry (the cloud warning is just a log)
        assert any(e["model_name"] == "default" for e in config["model_list"])

    def test_kilocode_sets_api_base(self):
        backends = [
            {
                "name": "kilocode",
                "type": "kilocode",
                "url": "http://kilocode.example.com/v1",
                "models": ["kimi-k2"],
                "api_key": "kilo-key",
            }
        ]
        config = generate_litellm_config(backends)
        entry = config["model_list"][0]
        assert entry["model_name"] == "kimi-k2"
        assert entry["litellm_params"]["api_base"] == "http://kilocode.example.com/v1"

    def test_openrouter_with_url_sets_api_base(self):
        backends = [
            {
                "name": "or",
                "type": "openrouter",
                "url": "https://openrouter.ai/api/v1",
                "models": ["auto"],
                "api_key": "or-key",
            }
        ]
        config = generate_litellm_config(backends)
        entry = config["model_list"][0]
        assert entry["litellm_params"]["api_base"] == "https://openrouter.ai/api/v1"

    def test_anthropic_no_api_base_set(self):
        backends = [
            {
                "name": "anth",
                "type": "anthropic",
                "models": ["claude-sonnet-4-20250514"],
                "api_key": "ant-key",
            }
        ]
        config = generate_litellm_config(backends)
        entry = config["model_list"][0]
        assert "api_base" not in entry["litellm_params"]

    def test_deepseek_no_extra_api_base(self):
        backends = [
            {
                "name": "ds",
                "type": "deepseek",
                "models": ["deepseek-chat"],
                "api_key": "ds-key",
            }
        ]
        config = generate_litellm_config(backends)
        entry = config["model_list"][0]
        # deepseek is native LiteLLM; no explicit api_base unless url given
        assert "api_base" not in entry["litellm_params"]


# ---------------------------------------------------------------------------
# generate_litellm_config -- priority sorting
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMPrioritySort:

    def test_backends_sorted_by_priority(self):
        backends = [
            {"name": "low", "type": "ollama", "url": "http://low:11434", "priority": 10},
            {"name": "high", "type": "ollama", "url": "http://high:11434", "priority": 1},
            {"name": "mid", "type": "ollama", "url": "http://mid:11434", "priority": 5},
        ]
        config = generate_litellm_config(backends)
        names = [e["metadata"]["backend_name"] for e in config["model_list"]]
        assert names == ["high", "mid", "low"]

    def test_same_priority_stable_order(self):
        backends = [
            {"name": "a", "type": "ollama", "url": "http://a:11434", "priority": 1},
            {"name": "b", "type": "ollama", "url": "http://b:11434", "priority": 1},
        ]
        config = generate_litellm_config(backends)
        names = [e["metadata"]["backend_name"] for e in config["model_list"]]
        assert names == ["a", "b"]


# ---------------------------------------------------------------------------
# generate_litellm_config -- embedding discovery
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMEmbeddingDiscovery:

    def test_discovered_embedding_model_registered(self):
        backends = [
            {"name": "ollama-local", "type": "ollama", "url": "http://h:11434"}
        ]
        discovered = {"http://h:11434": ["nomic-embed-text"]}
        config = generate_litellm_config(backends, discovered=discovered)
        ml = config["model_list"]
        # default entry + embedding entry + alias entry
        assert len(ml) == 3
        embed_entry = ml[1]
        assert embed_entry["model_name"] == "nomic-embed-text"
        assert embed_entry["litellm_params"]["model"] == "ollama/nomic-embed-text"
        assert embed_entry["model_info"]["mode"] == "embedding"
        alias_entry = ml[2]
        assert alias_entry["model_name"] == EMBEDDING_ALIAS
        assert alias_entry["model_info"]["mode"] == "embedding"

    def test_first_embedding_claims_alias(self):
        backends = [
            {"name": "o1", "type": "ollama", "url": "http://h1:11434"},
            {"name": "o2", "type": "ollama", "url": "http://h2:11434"},
        ]
        discovered = {
            "http://h1:11434": ["nomic-embed-text"],
            "http://h2:11434": ["mxbai-embed-large"],
        }
        config = generate_litellm_config(backends, discovered=discovered)
        alias_entries = [e for e in config["model_list"] if e["model_name"] == EMBEDDING_ALIAS]
        assert len(alias_entries) == 1
        # The alias should point to the first discovered embedding
        assert alias_entries[0]["litellm_params"]["model"] == "ollama/nomic-embed-text"

    def test_chat_models_in_discovered_not_registered_as_embedding(self):
        backends = [
            {"name": "ollama-local", "type": "ollama", "url": "http://h:11434"}
        ]
        discovered = {"http://h:11434": ["llama3.1-8b", "qwen2.5-7b"]}
        config = generate_litellm_config(backends, discovered=discovered)
        ml = config["model_list"]
        # Only the default entry; no embedding entries
        assert len(ml) == 1
        assert ml[0]["model_name"] == "default"

    def test_reranker_in_discovered_not_registered_as_embedding(self):
        backends = [
            {"name": "ollama-local", "type": "ollama", "url": "http://h:11434"}
        ]
        discovered = {"http://h:11434": ["bge-reranker-v2"]}
        config = generate_litellm_config(backends, discovered=discovered)
        ml = config["model_list"]
        assert len(ml) == 1

    def test_discovered_none_falls_back_to_probe(self):
        """When discovered has None for a URL, _discover_ollama_models is called."""
        backends = [
            {"name": "ollama-local", "type": "ollama", "url": "http://h:11434"}
        ]
        discovered = {"http://h:11434": None}
        with patch.object(cfg_mod, "_discover_ollama_models", return_value=[]):
            config = generate_litellm_config(backends, discovered=discovered)
        assert config["model_list"][0]["model_name"] == "default"

    def test_mixed_chat_and_embedding_discovered(self):
        backends = [
            {"name": "ollama-local", "type": "ollama", "url": "http://h:11434"}
        ]
        discovered = {"http://h:11434": ["llama3", "nomic-embed-text", "qwen2.5"]}
        config = generate_litellm_config(backends, discovered=discovered)
        ml = config["model_list"]
        # default + embedding + alias
        assert len(ml) == 3
        assert ml[0]["model_name"] == "default"
        assert ml[1]["model_name"] == "nomic-embed-text"
        assert ml[2]["model_name"] == EMBEDDING_ALIAS


# ---------------------------------------------------------------------------
# generate_litellm_config -- local backend with registry
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMLocalBackendModels:

    def _make_registry(self, installed, manifests):
        class _Reg:
            def list_installed(self):
                return installed
            def get(self, mid):
                return manifests.get(mid)
        return _Reg()

    def test_local_backend_registers_installed_models(self):
        backends = [
            {
                "name": "local-rk-llama-cpp",
                "type": "ollama",
                "url": "http://192.168.1.50:8080",
            }
        ]
        installed = [{"id": "gemma-4-e2b-gguf"}]
        manifest = type("M", (), {
            "type": "model",
            "variants": [
                {
                    "requires": {
                        "backends": [{"id": "rk-llama-cpp"}]
                    }
                }
            ],
        })()
        reg = self._make_registry(installed, {"gemma-4-e2b-gguf": manifest})
        config = generate_litellm_config(backends, registry=reg)
        ml = config["model_list"]
        # default + local-installed model
        assert len(ml) == 2
        local_entry = ml[1]
        assert local_entry["model_name"] == "gemma-4-e2b-gguf"
        assert local_entry["litellm_params"]["model"] == "ollama_chat/gemma-4-e2b-gguf"
        assert local_entry["litellm_params"]["api_base"] == "http://192.168.1.50:8080"
        assert local_entry["metadata"]["source"] == "local-installed"

    def test_non_local_backend_skips_registry(self):
        backends = [
            {"name": "ollama-local", "type": "ollama", "url": "http://h:11434"}
        ]
        config = generate_litellm_config(backends, registry=type("R", (), {})())
        assert len(config["model_list"]) == 1

    def test_none_registry_skips_local_models(self):
        backends = [
            {
                "name": "local-rk-llama-cpp",
                "type": "ollama",
                "url": "http://192.168.1.50:8080",
            }
        ]
        config = generate_litellm_config(backends, registry=None)
        assert len(config["model_list"]) == 1

    def test_local_model_deduplicated_per_backend(self):
        """Same manifest_id from the same backend should not produce duplicates."""
        backends = [
            {
                "name": "local-rk-llama-cpp",
                "type": "ollama",
                "url": "http://192.168.1.50:8080",
            }
        ]
        installed = [{"id": "gemma-4-e2b-gguf"}]
        manifest = type("M", (), {
            "type": "model",
            "variants": [
                {
                    "requires": {
                        "backends": [{"id": "rk-llama-cpp"}, {"id": "rk-llama-cpp"}]
                    }
                }
            ],
        })()
        reg = self._make_registry(installed, {"gemma-4-e2b-gguf": manifest})
        config = generate_litellm_config(backends, registry=reg)
        ml = config["model_list"]
        # Should still be 2 (default + one local entry), not 3
        assert len(ml) == 2

    def test_manifest_non_model_type_skipped(self):
        backends = [
            {
                "name": "local-rk-llama-cpp",
                "type": "ollama",
                "url": "http://192.168.1.50:8080",
            }
        ]
        installed = [{"id": "not-a-model"}]
        manifest = type("M", (), {"type": "dataset", "variants": []})()
        reg = self._make_registry(installed, {"not-a-model": manifest})
        config = generate_litellm_config(backends, registry=reg)
        assert len(config["model_list"]) == 1

    def test_manifest_without_get_method(self):
        """Registry without .get() should not crash."""
        backends = [
            {
                "name": "local-rk-llama-cpp",
                "type": "ollama",
                "url": "http://192.168.1.50:8080",
            }
        ]
        installed = [{"id": "some-model"}]

        class _Reg:
            def list_installed(self):
                return installed

        config = generate_litellm_config(backends, registry=_Reg())
        # some-model has no .get(), so manifest is None, skipped
        assert len(config["model_list"]) == 1


# ---------------------------------------------------------------------------
# generate_litellm_config -- api_key handling
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMApiKey:

    def test_api_key_direct(self):
        backends = [
            {
                "name": "custom",
                "type": "openai-compatible",
                "url": "http://custom:8080",
                "api_key": "sk-custom-key",
            }
        ]
        config = generate_litellm_config(backends)
        entry = config["model_list"][0]
        assert entry["litellm_params"]["api_key"] == "sk-custom-key"

    def test_api_key_secret_takes_precedence(self):
        backends = [
            {
                "name": "custom",
                "type": "openai-compatible",
                "url": "http://custom:8080",
                "api_key": "sk-direct",
                "api_key_secret": "MY_SECRET",
            }
        ]
        config = generate_litellm_config(backends)
        entry = config["model_list"][0]
        assert entry["litellm_params"]["api_key"] == "os.environ/MY_SECRET"

    def test_no_api_key_omitted(self):
        backends = [
            {"name": "ollama-local", "type": "ollama", "url": "http://h:11434"}
        ]
        config = generate_litellm_config(backends)
        entry = config["model_list"][0]
        assert "api_key" not in entry["litellm_params"]


# ---------------------------------------------------------------------------
# generate_litellm_config -- mixed backends
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMMixedBackends:

    def test_ollama_and_cloud(self):
        backends = [
            {"name": "local", "type": "ollama", "url": "http://h:11434", "priority": 1},
            {
                "name": "openai",
                "type": "openai",
                "models": ["gpt-4o"],
                "api_key": "sk-oai",
                "priority": 2,
            },
        ]
        config = generate_litellm_config(backends)
        ml = config["model_list"]
        # local default + openai gpt-4o + openai default
        assert len(ml) == 3
        assert ml[0]["metadata"]["backend_name"] == "local"
        assert ml[1]["model_name"] == "gpt-4o"
        assert ml[2]["model_name"] == "default"
        assert ml[2]["metadata"]["backend_name"] == "openai"

    def test_custom_default_model_name(self):
        backends = [
            {"name": "o", "type": "ollama", "url": "http://h:11434"}
        ]
        config = generate_litellm_config(backends, default_model="my-primary")
        assert config["model_list"][0]["model_name"] == "my-primary"


# ---------------------------------------------------------------------------
# _discover_ollama_models (network -- mocked)
# ---------------------------------------------------------------------------

class TestDiscoverOllamaModels:

    def test_returns_model_names_on_success(self):
        class _Resp:
            status_code = 200
            def json(self):
                return {"models": [{"name": "llama3"}, {"name": "qwen"}]}
        with patch("tinyagentos.litellm_config.httpx.get", return_value=_Resp()):
            result = _discover_ollama_models("http://h:11434")
        assert result == ["llama3", "qwen"]

    def test_returns_empty_on_non_200(self):
        class _Resp:
            status_code = 500
        with patch("tinyagentos.litellm_config.httpx.get", return_value=_Resp()):
            result = _discover_ollama_models("http://h:11434")
        assert result == []

    def test_returns_empty_on_exception(self):
        with patch("tinyagentos.litellm_config.httpx.get", side_effect=ConnectionError):
            result = _discover_ollama_models("http://h:11434")
        assert result == []

    def test_filters_out_models_without_name(self):
        class _Resp:
            status_code = 200
            def json(self):
                return {"models": [{"name": "llama3"}, {"size": 123}]}
        with patch("tinyagentos.litellm_config.httpx.get", return_value=_Resp()):
            result = _discover_ollama_models("http://h:11434")
        assert result == ["llama3"]


# ---------------------------------------------------------------------------
# _discover_ollama_backends_concurrent (async -- mocked)
# ---------------------------------------------------------------------------

class TestDiscoverOllamaBackendsConcurrent:

    @pytest.mark.asyncio
    async def test_probes_all_ollama_urls(self):
        backends = [
            {"name": "o1", "type": "ollama", "url": "http://h1:11434"},
            {"name": "o2", "type": "rkllama", "url": "http://h2:8080"},
        ]
        with patch(
            "tinyagentos.litellm_config._discover_ollama_models",
            side_effect=lambda url, timeout: (["llama3"] if "h1" in url else ["qwen"]),
        ):
            result = await _discover_ollama_backends_concurrent(backends)
        assert result == {"http://h1:11434": ["llama3"], "http://h2:8080": ["qwen"]}

    @pytest.mark.asyncio
    async def test_non_ollama_backends_skipped(self):
        backends = [
            {"name": "o", "type": "ollama", "url": "http://h:11434"},
            {"name": "openai", "type": "openai"},
        ]
        with patch(
            "tinyagentos.litellm_config._discover_ollama_models",
            return_value=["llama3"],
        ):
            result = await _discover_ollama_backends_concurrent(backends)
        assert result == {"http://h:11434": ["llama3"]}

    @pytest.mark.asyncio
    async def test_no_ollama_backends_returns_empty(self):
        backends = [
            {"name": "openai", "type": "openai"},
        ]
        result = await _discover_ollama_backends_concurrent(backends)
        assert result == {}

    @pytest.mark.asyncio
    async def test_exception_returns_empty_list_for_that_url(self):
        backends = [
            {"name": "o", "type": "ollama", "url": "http://h:11434"},
        ]
        with patch(
            "tinyagentos.litellm_config._discover_ollama_models",
            side_effect=ConnectionError("fail"),
        ):
            result = await _discover_ollama_backends_concurrent(backends)
        assert result == {"http://h:11434": []}

    @pytest.mark.asyncio
    async def test_backends_without_url_skipped(self):
        backends = [
            {"name": "o", "type": "ollama"},
        ]
        result = await _discover_ollama_backends_concurrent(backends)
        assert result == {}


# ---------------------------------------------------------------------------
# _local_backend_models_from_registry
# ---------------------------------------------------------------------------

class TestLocalBackendModelsFromRegistry:

    def _make_registry(self, installed, manifests):
        class _Reg:
            def list_installed(self):
                return installed
            def get(self, mid):
                return manifests.get(mid)
        return _Reg()

    def test_returns_empty_for_none_registry(self):
        backend = {"name": "local-foo"}
        assert _local_backend_models_from_registry(backend, None) == []

    def test_returns_empty_for_non_local_name(self):
        backend = {"name": "ollama-local"}
        reg = type("R", (), {})()
        assert _local_backend_models_from_registry(backend, reg) == []

    def test_returns_empty_for_empty_service_id(self):
        backend = {"name": "local-"}
        reg = type("R", (), {})()
        assert _local_backend_models_from_registry(backend, reg) == []

    def test_matches_installed_models(self):
        backend = {"name": "local-my-service"}
        installed = [{"id": "model-a"}, {"id": "model-b"}]
        manifest_a = type("M", (), {
            "type": "model",
            "variants": [{"requires": {"backends": [{"id": "my-service"}]}}],
        })()
        manifest_b = type("M", (), {
            "type": "model",
            "variants": [{"requires": {"backends": [{"id": "other-service"}]}}],
        })()
        manifests = {"model-a": manifest_a, "model-b": manifest_b}

        class _Reg:
            def list_installed(self):
                return installed
            def get(self, mid):
                return manifests.get(mid)

        result = _local_backend_models_from_registry(backend, _Reg())
        assert result == ["model-a"]

    def test_list_installed_exception_returns_empty(self):
        backend = {"name": "local-svc"}

        class _Reg:
            def list_installed(self):
                raise Exception("fail")

        assert _local_backend_models_from_registry(backend, _Reg()) == []

    def test_non_dict_variant_skipped(self):
        backend = {"name": "local-svc"}
        installed = [{"id": "m1"}]
        manifest = type("M", (), {
            "type": "model",
            "variants": ["not-a-dict"],
        })()
        reg = self._make_registry(installed, {"m1": manifest})
        assert _local_backend_models_from_registry(backend, reg) == []

    def test_deduplicates_matched_models(self):
        """A model matched via two variants should appear once."""
        backend = {"name": "local-svc"}
        installed = [{"id": "m1"}]
        manifest = type("M", (), {
            "type": "model",
            "variants": [
                {"requires": {"backends": [{"id": "svc"}]}},
                {"requires": {"backends": [{"id": "svc"}]}},
            ],
        })()
        reg = self._make_registry(installed, {"m1": manifest})
        result = _local_backend_models_from_registry(backend, reg)
        assert result == ["m1"]
