"""Unit tests for tinyagentos/litellm_config.py.

Tests the gateway model table (build_model_list) and model discovery in isolation: no
network calls, no live hardware reads.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import tinyagentos.litellm_config as cfg_mod
from tinyagentos.litellm_config import (
    build_model_list,
    _is_embedding_model,
    _local_backend_models_from_registry,
    _discover_ollama_models,
    _discover_ollama_backends_concurrent,
    EMBEDDING_ALIAS,
)


def _model_table(backends, **kwargs) -> dict:
    """The gateway routing table: ``build_model_list`` (the LiteLLM config
    writer that wrapped it is gone, LiteLLM removal stage 2b-2a)."""
    return {"model_list": build_model_list(backends, **kwargs)}


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
# model table -- structure
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMStructure:

    def test_empty_backends_empty_model_list(self):
        config = _model_table([])
        assert config["model_list"] == []


# ---------------------------------------------------------------------------
# model table -- single ollama backend
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMOllamaBackend:

    def test_single_backend_produces_default_entry(self):
        backends = [
            {"name": "ollama-local", "type": "ollama", "url": "http://localhost:11434"}
        ]
        config = _model_table(backends)
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
        config = _model_table(backends)
        entry = config["model_list"][0]
        assert entry["litellm_params"]["model"] == "ollama_chat/llama3"

    def test_url_trailing_slash_stripped(self):
        backends = [
            {"name": "o", "type": "ollama", "url": "http://host:11434/"}
        ]
        config = _model_table(backends)
        entry = config["model_list"][0]
        assert entry["litellm_params"]["api_base"] == "http://host:11434"

    def test_priority_passed_to_metadata(self):
        backends = [
            {"name": "o", "type": "ollama", "url": "http://h:11434", "priority": 5}
        ]
        config = _model_table(backends)
        assert config["model_list"][0]["metadata"]["priority"] == 5

    def test_default_priority_is_99(self):
        backends = [
            {"name": "o", "type": "ollama", "url": "http://h:11434"}
        ]
        config = _model_table(backends)
        assert config["model_list"][0]["metadata"]["priority"] == 99


# ---------------------------------------------------------------------------
# model table -- rkllama backend
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMRkllamaBackend:

    def test_rkllama_uses_ollama_chat_prefix(self):
        backends = [
            {"name": "rk-box", "type": "rkllama", "url": "http://192.168.1.50:8080"}
        ]
        config = _model_table(backends)
        entry = config["model_list"][0]
        assert entry["litellm_params"]["model"] == "ollama_chat/default"
        assert entry["litellm_params"]["api_base"] == "http://192.168.1.50:8080"


# ---------------------------------------------------------------------------
# model table -- hailo-ollama backend (S5)
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMHailoOllamaBackend:

    def test_hailo_ollama_uses_ollama_chat_prefix(self):
        """hailo-ollama is Ollama-compatible, so the default chat entry must use
        the ollama_chat prefix and point at the remapped port 7836."""
        backends = [
            {"name": "hailo-box", "type": "hailo-ollama", "url": "http://localhost:7836"}
        ]
        config = _model_table(backends)
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
        config = _model_table(backends, discovered=discovered)
        ml = config["model_list"]
        embed_entries = [e for e in ml if e.get("model_info", {}).get("mode") == "embedding"]
        assert len(embed_entries) >= 1
        assert embed_entries[0]["litellm_params"]["model"] == "ollama/nomic-embed-text"


# ---------------------------------------------------------------------------
# model table -- cloud backends
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
        config = _model_table(backends)
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
        config = _model_table(backends)
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
        config = _model_table(backends)
        entry = config["model_list"][0]
        assert entry["litellm_params"]["api_key"] == "os.environ/OPENAI_API_KEY"

    def test_cloud_backend_missing_url_or_models_logs_warning(self, caplog):
        backends = [
            {"name": "bad-cloud", "type": "openai"}
        ]
        config = _model_table(backends)
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
        config = _model_table(backends)
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
        config = _model_table(backends)
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
        config = _model_table(backends)
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
        config = _model_table(backends)
        entry = config["model_list"][0]
        # deepseek is native LiteLLM; no explicit api_base unless url given
        assert "api_base" not in entry["litellm_params"]


# ---------------------------------------------------------------------------
# model table -- priority sorting
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMPrioritySort:

    def test_backends_sorted_by_priority(self):
        backends = [
            {"name": "low", "type": "ollama", "url": "http://low:11434", "priority": 10},
            {"name": "high", "type": "ollama", "url": "http://high:11434", "priority": 1},
            {"name": "mid", "type": "ollama", "url": "http://mid:11434", "priority": 5},
        ]
        config = _model_table(backends)
        names = [e["metadata"]["backend_name"] for e in config["model_list"]]
        assert names == ["high", "mid", "low"]

    def test_same_priority_stable_order(self):
        backends = [
            {"name": "a", "type": "ollama", "url": "http://a:11434", "priority": 1},
            {"name": "b", "type": "ollama", "url": "http://b:11434", "priority": 1},
        ]
        config = _model_table(backends)
        names = [e["metadata"]["backend_name"] for e in config["model_list"]]
        assert names == ["a", "b"]


# ---------------------------------------------------------------------------
# model table -- embedding discovery
# ---------------------------------------------------------------------------

class TestGenerateLiteLLMEmbeddingDiscovery:

    def test_discovered_embedding_model_registered(self):
        backends = [
            {"name": "ollama-local", "type": "ollama", "url": "http://h:11434"}
        ]
        discovered = {"http://h:11434": ["nomic-embed-text"]}
        config = _model_table(backends, discovered=discovered)
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
        config = _model_table(backends, discovered=discovered)
        alias_entries = [e for e in config["model_list"] if e["model_name"] == EMBEDDING_ALIAS]
        assert len(alias_entries) == 1
        # The alias should point to the first discovered embedding
        assert alias_entries[0]["litellm_params"]["model"] == "ollama/nomic-embed-text"

    def test_chat_models_in_discovered_not_registered_as_embedding(self):
        backends = [
            {"name": "ollama-local", "type": "ollama", "url": "http://h:11434"}
        ]
        discovered = {"http://h:11434": ["llama3.1-8b", "qwen2.5-7b"]}
        config = _model_table(backends, discovered=discovered)
        ml = config["model_list"]
        # Only the default entry; no embedding entries
        assert len(ml) == 1
        assert ml[0]["model_name"] == "default"

    def test_reranker_in_discovered_not_registered_as_embedding(self):
        backends = [
            {"name": "ollama-local", "type": "ollama", "url": "http://h:11434"}
        ]
        discovered = {"http://h:11434": ["bge-reranker-v2"]}
        config = _model_table(backends, discovered=discovered)
        ml = config["model_list"]
        assert len(ml) == 1

    def test_discovered_none_falls_back_to_probe(self):
        """When discovered has None for a URL, _discover_ollama_models is called."""
        backends = [
            {"name": "ollama-local", "type": "ollama", "url": "http://h:11434"}
        ]
        discovered = {"http://h:11434": None}
        with patch.object(cfg_mod, "_discover_ollama_models", return_value=[]):
            config = _model_table(backends, discovered=discovered)
        assert config["model_list"][0]["model_name"] == "default"

    def test_mixed_chat_and_embedding_discovered(self):
        backends = [
            {"name": "ollama-local", "type": "ollama", "url": "http://h:11434"}
        ]
        discovered = {"http://h:11434": ["llama3", "nomic-embed-text", "qwen2.5"]}
        config = _model_table(backends, discovered=discovered)
        ml = config["model_list"]
        # default + embedding + alias
        assert len(ml) == 3
        assert ml[0]["model_name"] == "default"
        assert ml[1]["model_name"] == "nomic-embed-text"
        assert ml[2]["model_name"] == EMBEDDING_ALIAS


# ---------------------------------------------------------------------------
# model table -- local backend with registry
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
        config = _model_table(backends, registry=reg)
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
        config = _model_table(backends, registry=type("R", (), {})())
        assert len(config["model_list"]) == 1

    def test_none_registry_skips_local_models(self):
        backends = [
            {
                "name": "local-rk-llama-cpp",
                "type": "ollama",
                "url": "http://192.168.1.50:8080",
            }
        ]
        config = _model_table(backends, registry=None)
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
        config = _model_table(backends, registry=reg)
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
        config = _model_table(backends, registry=reg)
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

        config = _model_table(backends, registry=_Reg())
        # some-model has no .get(), so manifest is None, skipped
        assert len(config["model_list"]) == 1


# ---------------------------------------------------------------------------
# model table -- api_key handling
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
        config = _model_table(backends)
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
        config = _model_table(backends)
        entry = config["model_list"][0]
        assert entry["litellm_params"]["api_key"] == "os.environ/MY_SECRET"

    def test_no_api_key_omitted(self):
        backends = [
            {"name": "ollama-local", "type": "ollama", "url": "http://h:11434"}
        ]
        config = _model_table(backends)
        entry = config["model_list"][0]
        assert "api_key" not in entry["litellm_params"]


# ---------------------------------------------------------------------------
# model table -- mixed backends
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
        config = _model_table(backends)
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
        config = _model_table(backends, default_model="my-primary")
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
