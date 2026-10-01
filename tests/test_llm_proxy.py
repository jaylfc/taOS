"""The per-agent key service (``LLMProxy``) and the gateway's model table.

LiteLLM removal stage 2b-2a deleted the LiteLLM process this file used to
test (spawn, config writer, self-heal, stderr log, readiness poll). What is
left: key mint / re-scope / delete with no process, the model table the
gateway routes with, and the scoped-key helper.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest
from tinyagentos.litellm_config import _is_embedding_model, build_model_list
from tinyagentos.llm_proxy import EMBEDDING_ALIAS, LLMProxy


def _model_table(backends, **kwargs) -> dict:
    """The gateway routing table (the LiteLLM config writer it used to feed is gone)."""
    return {"model_list": build_model_list(backends, **kwargs)}


class TestKeyService:
    """Key admin is local: no process, no LiteLLM, no master key."""

    def test_default_port_is_7834(self):
        assert LLMProxy().port == 7834

    def test_has_no_process_api(self):
        proxy = LLMProxy(port=4000, data_dir=Path("/nonexistent"))
        for gone in ("start", "stop", "is_running", "reload_config", "write_config", "url"):
            assert not hasattr(proxy, gone), gone

    @pytest.mark.asyncio
    async def test_create_agent_key_mints_locally(self, tmp_path):
        from tinyagentos.litellm_keystore import LiteLLMKeyStore, default_keystore_path

        key = await LLMProxy(port=4000, data_dir=tmp_path).create_agent_key("routing-only")
        assert LiteLLMKeyStore(default_keystore_path(tmp_path)).lookup(key) == {
            "agent": "routing-only", "allowed_models": ["default", EMBEDDING_ALIAS]}

    @pytest.mark.asyncio
    async def test_create_agent_key_returns_none_and_warns_when_the_mint_raises(self, tmp_path, caplog, monkeypatch):
        """When the local key store mint raises, create_agent_key returns None
        and logs a warning (the deployer then refuses the deploy)."""
        import logging
        import tinyagentos.llm_proxy as mod

        class _FakeStore:
            def mint(self, *a, **kw):
                raise OSError("disk full")

        monkeypatch.setattr(mod.LLMProxy, "_keystore", lambda self: _FakeStore())

        proxy = mod.LLMProxy(port=4000, data_dir=tmp_path)
        with caplog.at_level(logging.WARNING, logger="tinyagentos.llm_proxy"):
            key = await proxy.create_agent_key("boom")
        assert key is None
        assert any("key store mint failed for boom" in r.getMessage() for r in caplog.records)

    @pytest.mark.asyncio
    async def test_create_agent_key_without_data_dir_mints_nothing(self):
        """No data dir: the key store location is unknown, so no key (never a
        world-shared /tmp store)."""
        assert await LLMProxy(port=4000).create_agent_key("nowhere") is None

    @pytest.mark.asyncio
    async def test_create_key_scopes_models(self, tmp_path):
        proxy = LLMProxy(port=14004, data_dir=tmp_path)
        key = await proxy.create_agent_key("agent-a", ["gpt-4o"])
        assert key and key.startswith("sk-taos-")
        assert proxy._keystore().lookup(key)["agent"] == "agent-a"

    @pytest.mark.asyncio
    async def test_create_key_no_models_scopes_to_default(self, tmp_path):
        """An agent deployed without an explicit model is scoped to the default
        alias, not an empty allowlist (which the gateway deny-alls)."""
        proxy = LLMProxy(port=14006, data_dir=tmp_path)
        key = await proxy.create_agent_key("agent-a", None)
        assert proxy._keystore().lookup(key)["allowed_models"] == ["default", EMBEDDING_ALIAS]

    @pytest.mark.asyncio
    async def test_update_and_delete_key(self, tmp_path):
        proxy = LLMProxy(port=14005, data_dir=tmp_path)
        key = await proxy.create_agent_key("agent-a", ["a"])
        assert await proxy.update_agent_key(key, ["b", "c"]) is True
        assert proxy._keystore().lookup(key)["allowed_models"] == ["b", "c", EMBEDDING_ALIAS]
        assert await proxy.delete_agent_key(key) is True
        assert proxy._keystore().lookup(key) is None

    @pytest.mark.asyncio
    async def test_key_usage_reads_budget_store(self, tmp_path):
        proxy = LLMProxy(port=14007, data_dir=tmp_path)
        key = await proxy.create_agent_key("spender", ["a"], max_budget=5.0)
        proxy._budget_store().add_spend("spender", 1.25)
        usage = await proxy.get_key_usage(key)
        assert usage["key"] is None
        assert usage["info"]["spend"] == pytest.approx(1.25)
        assert usage["info"]["max_budget"] == pytest.approx(5.0)


class TestConfigGeneration:
    def test_generates_config_from_backends(self):
        backends = [
            {"name": "fedora-gpu", "type": "ollama", "url": "http://fedora:11434", "priority": 1},
            {"name": "local-rkllama", "type": "rkllama", "url": "http://localhost:8080", "priority": 3},
        ]
        config = _model_table(backends)
        assert "model_list" in config
        assert len(config["model_list"]) >= 2
        # First entry should be highest priority
        assert config["model_list"][0]["litellm_params"]["api_base"] == "http://fedora:11434"

    def test_empty_backends_returns_empty_model_list(self):
        config = _model_table([])
        assert config["model_list"] == []

    def test_ollama_backend_uses_ollama_prefix(self):
        backends = [{"name": "local", "type": "ollama", "url": "http://localhost:11434", "priority": 1}]
        config = _model_table(backends)
        model_param = config["model_list"][0]["litellm_params"]["model"]
        assert model_param.startswith("ollama/") or model_param.startswith("ollama_chat/")

    def test_openai_backend_uses_direct_model(self):
        backends = [{"name": "cloud", "type": "openai", "url": "https://api.openai.com", "priority": 1, "api_key_secret": "openai-key"}]
        config = _model_table(backends)
        assert "api_base" not in config["model_list"][0]["litellm_params"] or config["model_list"][0]["litellm_params"]["api_base"] == "https://api.openai.com"

    def test_rkllama_treated_as_ollama_compat(self):
        backends = [{"name": "npu", "type": "rkllama", "url": "http://localhost:8080", "priority": 1}]
        config = _model_table(backends)
        # rkllama is ollama-compatible
        model_param = config["model_list"][0]["litellm_params"]["model"]
        assert "ollama" in model_param.lower() or config["model_list"][0]["litellm_params"].get("api_base")


class TestEmbeddingDiscovery:
    def test_classifier_recognises_common_embedding_names(self):
        assert _is_embedding_model("qwen3-embedding-0.6b")
        assert _is_embedding_model("bge-large-en-v1.5")
        assert _is_embedding_model("nomic-embed-text-v1.5")
        assert _is_embedding_model("mxbai-embed-large")

    def test_classifier_rejects_chat_and_reranker_models(self):
        assert not _is_embedding_model("llama3-8b")
        assert not _is_embedding_model("qwen3-4b-q4")
        # Rerankers include the word "embed" sometimes, but we skip
        # rerankers explicitly because LiteLLM doesn't front them yet.
        assert not _is_embedding_model("qwen3-reranker-0.6b")
        assert not _is_embedding_model("bge-reranker-v2-m3")

    def test_embedding_model_registered_with_stable_alias(self):
        """First embedding model discovered claims the stable taos-embedding-default
        alias so the deployer can inject one name for every install."""
        backends = [
            {"name": "npu", "type": "rkllama", "url": "http://localhost:8080", "priority": 1},
        ]
        with patch(
            "tinyagentos.litellm_config._discover_ollama_models",
            return_value=["qwen3-4b-chat", "qwen3-embedding-0.6b", "qwen3-reranker-0.6b"],
        ):
            config = _model_table(backends)

        names = [e["model_name"] for e in config["model_list"]]
        # Chat default entry still present
        assert "default" in names
        # Embedding model registered under its concrete name
        assert "qwen3-embedding-0.6b" in names
        # ...and under the stable alias the deployer injects
        assert EMBEDDING_ALIAS in names
        # Reranker is skipped
        assert "qwen3-reranker-0.6b" not in names

        # The alias and concrete entries must both be marked as embedding
        alias_entry = next(e for e in config["model_list"] if e["model_name"] == EMBEDDING_ALIAS)
        assert alias_entry.get("model_info", {}).get("mode") == "embedding"
        assert alias_entry["litellm_params"]["api_base"] == "http://localhost:8080"
        assert alias_entry["litellm_params"]["model"].startswith("ollama/")

    def test_no_embedding_entries_when_probe_empty(self):
        """Backend offline / probe fails → degrade gracefully with chat only."""
        backends = [
            {"name": "npu", "type": "rkllama", "url": "http://localhost:8080", "priority": 1},
        ]
        with patch("tinyagentos.litellm_config._discover_ollama_models", return_value=[]):
            config = _model_table(backends)
        names = [e["model_name"] for e in config["model_list"]]
        assert names == ["default"]

    def test_first_backend_claims_alias_only_once(self):
        """Multiple backends each serving embedding models should not fight for
        the alias — first-sorted-by-priority wins, others still register under
        their concrete names so clients can pin a specific backend."""
        backends = [
            {"name": "a", "type": "rkllama", "url": "http://a:8080", "priority": 1},
            {"name": "b", "type": "ollama", "url": "http://b:11434", "priority": 2},
        ]
        def _fake_probe(url, timeout=2.0):
            return ["bge-small-en-v1.5"] if "a" in url else ["nomic-embed-text-v1.5"]

        with patch("tinyagentos.litellm_config._discover_ollama_models", side_effect=_fake_probe):
            config = _model_table(backends)

        alias_entries = [e for e in config["model_list"] if e["model_name"] == EMBEDDING_ALIAS]
        assert len(alias_entries) == 1
        # Priority-1 backend ("a") won the alias
        assert alias_entries[0]["litellm_params"]["api_base"] == "http://a:8080"
        # Both concrete embedding names are still registered
        names = [e["model_name"] for e in config["model_list"]]
        assert "bge-small-en-v1.5" in names
        assert "nomic-embed-text-v1.5" in names


class TestCloudBackends:
    def test_generate_config_kilocode_backend(self):
        backends = [{
            "name": "kilo-free",
            "type": "kilocode",
            "url": "https://kilocode.ai/api/v1",
            "priority": 10,
            "api_key_secret": "KILOCODE_API_KEY",
            "models": ["kilo/free/claude-3.5-sonnet", "kilo/free/gpt-4o"],
        }]
        cfg = _model_table(backends)
        names = [e["model_name"] for e in cfg["model_list"]]
        assert "default" in names
        assert "kilo/free/claude-3.5-sonnet" in names
        assert "kilo/free/gpt-4o" in names
        kilo_entry = next(e for e in cfg["model_list"] if e["model_name"] == "kilo/free/claude-3.5-sonnet")
        assert kilo_entry["litellm_params"]["model"].startswith("openai/")
        assert kilo_entry["litellm_params"]["api_base"] == "https://kilocode.ai/api/v1"
        assert kilo_entry["litellm_params"]["api_key"] == "os.environ/KILOCODE_API_KEY"

    def test_generate_config_openrouter_backend(self):
        backends = [{
            "name": "or",
            "type": "openrouter",
            "url": "https://openrouter.ai/api/v1",
            "priority": 5,
            "api_key": "or-test-key",
            "models": [{"id": "meta-llama/llama-3-70b"}],
        }]
        cfg = _model_table(backends)
        model_entry = next(e for e in cfg["model_list"] if e["model_name"] == "meta-llama/llama-3-70b")
        assert model_entry["litellm_params"]["model"].startswith("openrouter/")
        assert model_entry["litellm_params"]["api_key"] == "or-test-key"

    def test_generate_config_cloud_without_models_only_default(self):
        backends = [{
            "name": "blank",
            "type": "openrouter",
            "url": "https://openrouter.ai/api/v1",
            "api_key": "x",
        }]
        cfg = _model_table(backends)
        assert [e["model_name"] for e in cfg["model_list"]] == ["default"]

    def test_generate_config_warns_on_incomplete_cloud_backend(self, caplog):
        """A cloud-type backend missing ``url`` or ``models`` should fire
        a WARNING so silent drops surface in logs. Historical kilocode
        regression slipped through precisely because this path was mute."""
        import logging
        backends = [
            {"name": "headless-kilo", "type": "kilocode", "priority": 5,
             "api_key_secret": "KILO_KEY"},
            {"name": "blank-openrouter", "type": "openrouter",
             "url": "https://openrouter.ai/api/v1", "priority": 6},
        ]
        with caplog.at_level(logging.WARNING, logger="tinyagentos.litellm_config"):
            _model_table(backends)

        msgs = [r.getMessage() for r in caplog.records]
        assert any(
            "headless-kilo" in m and "missing url or models" in m and "type=kilocode" in m
            for m in msgs
        ), msgs
        assert any(
            "blank-openrouter" in m and "missing url or models" in m
            for m in msgs
        ), msgs

    def test_generate_config_no_warning_on_complete_cloud_backend(self, caplog):
        """Well-formed cloud entries (url + models) must not trigger the
        incomplete-backend warning — otherwise operators lose the signal."""
        import logging
        backends = [{
            "name": "ok-kilo", "type": "kilocode",
            "url": "https://api.kilo.ai/api/gateway",
            "models": [{"id": "kilo-auto/free"}],
            "api_key_secret": "KILO_KEY",
        }]
        with caplog.at_level(logging.WARNING, logger="tinyagentos.litellm_config"):
            _model_table(backends)
        assert not any(
            "missing url or models" in r.getMessage() for r in caplog.records
        )

    def test_generate_config_ollama_backend_unchanged(self):
        backends = [{
            "name": "pi",
            "type": "ollama",
            "url": "http://localhost:11434",
            "priority": 10,
            "model": "llama3.2",
        }]
        cfg = _model_table(backends)
        chat = next(e for e in cfg["model_list"] if e["model_name"] == "default")
        assert chat["litellm_params"]["model"] == "ollama_chat/llama3.2"
        assert chat["litellm_params"]["api_base"] == "http://localhost:11434"


class TestSystemdUnitPermissions:
    """S2-10: the systemd unit template must enable PrivateTmp. It must NOT
    set a global UMask: the model store under <data_dir>/models is read by
    backend units (llama-cpp, hailo, rk*) that install-*.sh runs as a
    different user (the human $SUDO_USER), so a controller-wide umask would
    make every freshly downloaded model unreadable by those units. Per-file
    modes (mkdir 0700 / atomic_write_text 0o600 / os.open 0o600) are the
    hardening mechanism for this PR's secrets, not a global umask."""

    _UNIT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "systemd" / "tinyagentos.service"

    def test_unit_has_private_tmp(self):
        text = self._UNIT_PATH.read_text()
        assert re.search(r"(?m)^\s*PrivateTmp\s*=\s*yes\s*$", text)

    def test_unit_has_no_umask(self):
        text = self._UNIT_PATH.read_text()
        assert re.search(r"(?m)^\s*UMask\s*=", text) is None


class TestScopedKeyModels:
    """scoped_key_models is the single source of truth for a new key's model list."""

    def test_none_returns_default_and_embedding(self):
        from tinyagentos.llm_proxy import scoped_key_models
        assert scoped_key_models(None) == ["default", EMBEDDING_ALIAS]

    def test_single_model_returns_model_and_embedding(self):
        from tinyagentos.llm_proxy import scoped_key_models
        assert scoped_key_models(["m"]) == ["m", EMBEDDING_ALIAS]

    def test_alias_already_present_no_duplicate(self):
        from tinyagentos.llm_proxy import scoped_key_models
        assert scoped_key_models(["m", EMBEDDING_ALIAS]) == ["m", EMBEDDING_ALIAS]

    def test_order_models_first_alias_last(self):
        from tinyagentos.llm_proxy import scoped_key_models
        assert scoped_key_models(["a", "b"]) == ["a", "b", EMBEDDING_ALIAS]
