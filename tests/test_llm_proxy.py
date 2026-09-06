from __future__ import annotations

import re
import stat
from pathlib import Path
from unittest.mock import patch

import pytest
from tinyagentos.llm_proxy import (
    EMBEDDING_ALIAS,
    _is_embedding_model,
    generate_litellm_config,
    LLMProxy,
)
from tinyagentos.litellm_config import get_litellm_master_key


class TestConfigGeneration:
    def test_generates_config_from_backends(self):
        backends = [
            {"name": "fedora-gpu", "type": "ollama", "url": "http://fedora:11434", "priority": 1},
            {"name": "local-rkllama", "type": "rkllama", "url": "http://localhost:8080", "priority": 3},
        ]
        config = generate_litellm_config(backends)
        assert "model_list" in config
        assert len(config["model_list"]) >= 2
        # First entry should be highest priority
        assert config["model_list"][0]["litellm_params"]["api_base"] == "http://fedora:11434"

    def test_empty_backends_returns_empty_model_list(self):
        config = generate_litellm_config([])
        assert config["model_list"] == []

    def test_config_emits_master_key(self, tmp_path):
        """general_settings.master_key must carry the per-install taOS master
        key (generated and persisted on first use) so LiteLLM rejects
        unauthenticated requests and every internal admin call uses the same value."""
        key = get_litellm_master_key(tmp_path)
        config = generate_litellm_config([], master_key=key)
        assert config["general_settings"]["master_key"] == key
        assert config["general_settings"]["master_key"].startswith("sk-taos-")

    def test_ollama_backend_uses_ollama_prefix(self):
        backends = [{"name": "local", "type": "ollama", "url": "http://localhost:11434", "priority": 1}]
        config = generate_litellm_config(backends)
        model_param = config["model_list"][0]["litellm_params"]["model"]
        assert model_param.startswith("ollama/") or model_param.startswith("ollama_chat/")

    def test_openai_backend_uses_direct_model(self):
        backends = [{"name": "cloud", "type": "openai", "url": "https://api.openai.com", "priority": 1, "api_key_secret": "openai-key"}]
        config = generate_litellm_config(backends)
        assert "api_base" not in config["model_list"][0]["litellm_params"] or config["model_list"][0]["litellm_params"]["api_base"] == "https://api.openai.com"

    def test_rkllama_treated_as_ollama_compat(self):
        backends = [{"name": "npu", "type": "rkllama", "url": "http://localhost:8080", "priority": 1}]
        config = generate_litellm_config(backends)
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
            config = generate_litellm_config(backends)

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
            config = generate_litellm_config(backends)
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
            config = generate_litellm_config(backends)

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
        cfg = generate_litellm_config(backends)
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
        cfg = generate_litellm_config(backends)
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
        cfg = generate_litellm_config(backends)
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
            generate_litellm_config(backends)

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
            generate_litellm_config(backends)
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
        cfg = generate_litellm_config(backends)
        chat = next(e for e in cfg["model_list"] if e["model_name"] == "default")
        assert chat["litellm_params"]["model"] == "ollama_chat/llama3.2"
        assert chat["litellm_params"]["api_base"] == "http://localhost:11434"


class TestCallbackWiring:
    def test_config_emits_callbacks_under_litellm_settings(self):
        """Callbacks must be emitted under ``litellm_settings.callbacks`` as a
        single dotted path string so LiteLLM's ``get_instance_fn`` loader can
        resolve it relative to the config file directory. Historically the
        callback lived in ``general_settings.custom_callbacks`` which LiteLLM
        silently ignored — leaving trace events empty."""
        result = generate_litellm_config([])
        assert result["litellm_settings"]["callbacks"] == (
            "taos_callback.proxy_handler_instance"
        )
        assert "custom_callbacks" not in result["general_settings"]

    @pytest.mark.asyncio
    async def test_write_config_creates_callback_shim(self, tmp_path):
        """``write_config`` writes a sibling ``taos_callback.py`` next to
        the generated yaml, re-exporting the installed callback instance as
        ``proxy_handler_instance`` — so LiteLLM's config-dir-relative import
        succeeds without duplicating the callback source."""
        proxy = LLMProxy(port=14000, config_dir=tmp_path)
        await proxy.write_config([])
        shim = tmp_path / "taos_callback.py"
        assert shim.exists()
        contents = shim.read_text()
        assert (
            "from tinyagentos.litellm_callback import taos_callback "
            "as proxy_handler_instance"
        ) in contents


class TestLLMProxy:
    def test_default_port_is_7834(self):
        proxy = LLMProxy()
        assert proxy.port == 7834

    def test_config_provided_port_overrides_default(self):
        proxy = LLMProxy(port=4000)
        assert proxy.port == 4000

    def test_proxy_not_running_initially(self):
        proxy = LLMProxy(port=14000)
        assert not proxy.is_running()

    def test_proxy_url(self):
        proxy = LLMProxy(port=14000)
        assert proxy.url == "http://localhost:14000"

    def test_proxy_database_url_defaults_to_none(self):
        proxy = LLMProxy(port=14000)
        assert proxy.database_url is None

    def test_proxy_database_url_persisted(self):
        proxy = LLMProxy(port=14000, database_url="postgresql://u:p@h/db")
        assert proxy.database_url == "postgresql://u:p@h/db"


class TestDatabaseUrlPropagation:
    @pytest.mark.asyncio
    async def test_start_passes_database_url_when_set(self, monkeypatch):
        """DATABASE_URL lands in the litellm subprocess env when configured."""
        import shutil
        import tinyagentos.llm_proxy as mod

        class _FakeClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *exc): return False
            async def get(self, url):
                raise RuntimeError("no proxy")

        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeClient)
        monkeypatch.setattr(mod, "_pids_listening_on", lambda port: [])
        monkeypatch.setattr(shutil, "which", lambda _: "/fake/litellm")

        captured: dict = {}

        class _FakePopen:
            def __init__(self, *args, **kwargs):
                captured["env"] = kwargs.get("env") or {}
                # Raise so start() exits without spawning; the env we
                # cared about was already captured.
                raise FileNotFoundError("stubbed")

        monkeypatch.setattr(mod.subprocess, "Popen", _FakePopen)

        p = mod.LLMProxy(port=14001, database_url="postgresql://fake:pw@host/db")
        await p.start(backends=[])

        assert captured["env"]["DATABASE_URL"] == "postgresql://fake:pw@host/db"
        assert captured["env"]["LITELLM_MASTER_KEY"].startswith("sk-taos-")

    @pytest.mark.asyncio
    async def test_start_omits_database_url_when_unset(self, monkeypatch):
        """No DATABASE_URL in env when the proxy was built without one."""
        import shutil
        import tinyagentos.llm_proxy as mod

        class _FakeClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *exc): return False
            async def get(self, url):
                raise RuntimeError("no proxy")

        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeClient)
        monkeypatch.setattr(mod, "_pids_listening_on", lambda port: [])
        monkeypatch.setattr(shutil, "which", lambda _: "/fake/litellm")
        # Scrub any ambient DATABASE_URL from the test runner so we can
        # assert the proxy didn't invent one.
        monkeypatch.delenv("DATABASE_URL", raising=False)

        captured: dict = {}

        class _FakePopen:
            def __init__(self, *args, **kwargs):
                captured["env"] = kwargs.get("env") or {}
                raise FileNotFoundError("stubbed")

        monkeypatch.setattr(mod.subprocess, "Popen", _FakePopen)

        p = mod.LLMProxy(port=14002)
        await p.start(backends=[])

        assert "DATABASE_URL" not in captured["env"]


class TestLLMProxyOwnership:
    def test_is_running_false_by_default(self):
        from tinyagentos.llm_proxy import LLMProxy
        p = LLMProxy(port=4000)
        assert p.is_running() is False

    @pytest.mark.asyncio
    async def test_start_kills_foreign_process_on_port(self, monkeypatch):
        """When another process is already on :4000, start() must SIGTERM
        it rather than adopt — a foreign proxy could be holding a stale
        config or different master key, which would make /key/generate
        fail silently downstream."""
        import tinyagentos.llm_proxy as mod

        class _FakeResp:
            status_code = 200

        class _FakeClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *exc): return False
            async def get(self, url): return _FakeResp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeClient)

        foreign_pid = 424242
        monkeypatch.setattr(mod, "_pids_listening_on", lambda port: [foreign_pid])
        # Once killed, report dead so start() doesn't escalate to SIGKILL.
        monkeypatch.setattr(mod, "_pid_alive", lambda pid: False)

        kill_calls: list[tuple[int, int]] = []

        def _fake_kill(pid, sig):
            kill_calls.append((pid, sig))

        monkeypatch.setattr(mod.os, "kill", _fake_kill)

        # Short-circuit the spawn — we only care about the kill path.
        class _FakePopen:
            def __init__(self, *a, **kw):
                raise FileNotFoundError("stubbed to skip real spawn")

        monkeypatch.setattr(mod.subprocess, "Popen", _FakePopen)
        # Avoid resolving a real litellm binary on the test host.
        import tinyagentos.litellm_config as litellm_cfg_mod
        monkeypatch.setattr(litellm_cfg_mod, "_discover_ollama_models", lambda *a, **kw: [])

        p = mod.LLMProxy(port=4000)
        await p.start(backends=[])

        # SIGTERM must have been sent to the foreign PID before the
        # spawn attempt.
        assert (foreign_pid, mod.signal.SIGTERM) in kill_calls

    @pytest.mark.asyncio
    async def test_create_agent_key_logs_on_non_200(self, monkeypatch, caplog):
        """Non-200 from /key/generate must surface in logs so operators
        can see master-key mismatches / model-list rejections instead of
        hunting through null llm_key fields."""
        import logging
        import tinyagentos.llm_proxy as mod

        class _FakeResp:
            status_code = 401
            text = "Invalid master key"

        class _FakeClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *exc): return False
            async def post(self, url, json=None, headers=None): return _FakeResp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeClient)

        # database_url required so create_agent_key actually hits the
        # endpoint — without it the routing-only short-circuit returns
        # None before any HTTP call.
        p = mod.LLMProxy(port=4000, database_url="postgres://x:y@h/litellm")

        # Bypass is_running(): pretend we own a live subprocess.
        class _FakeProc:
            def poll(self): return None
        p._process = _FakeProc()

        with caplog.at_level(logging.WARNING, logger="tinyagentos.llm_proxy"):
            key = await p.create_agent_key("bridgetest")

        assert key is None
        assert any(
            "/key/generate" in rec.getMessage() and "401" in rec.getMessage()
            for rec in caplog.records
        ), [rec.getMessage() for rec in caplog.records]

    @pytest.mark.asyncio
    async def test_create_agent_key_skips_call_when_no_database_url(self, monkeypatch):
        """In routing-only mode (no Postgres), create_agent_key must
        return None without hitting /key/generate — otherwise LiteLLM
        emits a confusing 500 'DB not connected' on every deploy."""
        import tinyagentos.llm_proxy as mod

        called = False

        class _FakeClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *exc): return False
            async def post(self, *a, **kw):
                nonlocal called
                called = True
                raise AssertionError("/key/generate should not be called when database_url is None")

        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeClient)

        p = mod.LLMProxy(port=4000)  # no database_url

        class _FakeProc:
            def poll(self): return None
        p._process = _FakeProc()

        key = await p.create_agent_key("routing-only")
        assert key is None
        assert called is False


class TestInhouseKeys:
    """In-house key mode: per-agent keys minted in a local SQLite store via
    the custom_auth hook, so virtual keys work with no DATABASE_URL (the ARM /
    no-Postgres fix). Opt-in; default behavior is unchanged."""

    def test_config_emits_custom_auth_when_inhouse(self):
        result = generate_litellm_config([], inhouse_keys=True)
        gs = result["general_settings"]
        assert gs["custom_auth"] == "taos_auth.user_api_key_auth"
        assert gs["custom_auth_run_common_checks"] is False

    def test_config_no_custom_auth_by_default(self):
        result = generate_litellm_config([])
        assert "custom_auth" not in result["general_settings"]

    @pytest.mark.asyncio
    async def test_write_config_writes_auth_shim_when_inhouse(self, tmp_path):
        proxy = LLMProxy(port=14002, config_dir=tmp_path, inhouse_keys=True)
        await proxy.write_config([])
        shim = tmp_path / "taos_auth.py"
        assert shim.exists()
        assert "from tinyagentos.litellm_auth import user_api_key_auth" in shim.read_text()

    @pytest.mark.asyncio
    async def test_write_config_no_auth_shim_by_default(self, tmp_path):
        proxy = LLMProxy(port=14003, config_dir=tmp_path)
        await proxy.write_config([])
        assert not (tmp_path / "taos_auth.py").exists()

    @pytest.mark.asyncio
    async def test_create_key_mints_locally_without_db(self, tmp_path):
        """Minting works with no DB and no running proxy in in-house mode."""
        proxy = LLMProxy(port=14004, config_dir=tmp_path, data_dir=tmp_path,
                         inhouse_keys=True)
        key = await proxy.create_agent_key("agent-a", ["gpt-4o"])
        assert key and key.startswith("sk-taos-")
        # the same key authorizes via the shared store
        assert proxy._keystore().lookup(key)["agent"] == "agent-a"

    @pytest.mark.asyncio
    async def test_create_key_no_models_scopes_to_default(self, tmp_path):
        """Parity with the Postgres path's ``models or ["default"]``: an agent
        deployed without an explicit model is scoped to the default alias, not
        an empty allowlist (which the auth hook deny-alls)."""
        proxy = LLMProxy(port=14006, config_dir=tmp_path, data_dir=tmp_path,
                         inhouse_keys=True)
        key = await proxy.create_agent_key("agent-a", None)
        assert proxy._keystore().lookup(key)["allowed_models"] == ["default"]

    @pytest.mark.asyncio
    async def test_update_and_delete_key_inhouse(self, tmp_path):
        proxy = LLMProxy(port=14005, config_dir=tmp_path, data_dir=tmp_path,
                         inhouse_keys=True)
        key = await proxy.create_agent_key("agent-a", ["a"])
        assert await proxy.update_agent_key(key, ["b", "c"]) is True
        assert proxy._keystore().lookup(key)["allowed_models"] == ["b", "c"]
        assert await proxy.delete_agent_key(key) is True
        assert proxy._keystore().lookup(key) is None


class TestProxySelfHeal:
    """The proxy self-installs the litellm extra once if a pre-fix update
    stripped it (bounded + non-fatal), so agents are not left without a route."""

    @pytest.mark.asyncio
    async def test_selfheal_uses_uv_sync_extra_proxy(self, tmp_path, monkeypatch):
        import sys
        import tinyagentos.llm_proxy as mod

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "tinyagentos"\n')
        binp = tmp_path / ".local" / "bin"
        binp.mkdir(parents=True)
        (binp / "uv").write_text("x")
        monkeypatch.setattr(sys, "executable", str(tmp_path / ".venv" / "bin" / "python"))

        captured = {}

        class FakeProc:
            returncode = 0

            async def communicate(self):
                return (b"ok", None)

        async def fake_exec(*cmd, **kw):
            captured["cmd"] = list(cmd)
            captured["cwd"] = kw.get("cwd")
            captured["home"] = (kw.get("env") or {}).get("HOME")
            return FakeProc()

        monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", fake_exec)

        ok = await LLMProxy()._selfheal_proxy_extra()
        assert ok is True
        assert captured["cmd"][:4] == [str(binp / "uv"), "sync", "--frozen", "--extra"]
        assert "proxy" in captured["cmd"]
        assert captured["cwd"] == str(tmp_path)
        assert captured["home"] == str(tmp_path)

    @pytest.mark.asyncio
    async def test_selfheal_nonzero_returns_false(self, tmp_path, monkeypatch):
        import sys
        import tinyagentos.llm_proxy as mod

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "tinyagentos"\n')
        (tmp_path / ".local" / "bin").mkdir(parents=True)
        (tmp_path / ".local" / "bin" / "uv").write_text("x")
        monkeypatch.setattr(sys, "executable", str(tmp_path / ".venv" / "bin" / "python"))

        class FakeProc:
            returncode = 1

            async def communicate(self):
                return (b"boom", None)

        async def fake_exec(*cmd, **kw):
            return FakeProc()

        monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", fake_exec)
        assert await LLMProxy()._selfheal_proxy_extra() is False

    @pytest.mark.asyncio
    async def test_selfheal_skips_when_no_install_root(self, tmp_path, monkeypatch):
        import sys
        monkeypatch.setattr(sys, "executable", str(tmp_path / ".venv" / "bin" / "python"))
        # No pyproject.toml under tmp_path -> cannot locate install root.
        assert await LLMProxy()._selfheal_proxy_extra() is False

    @pytest.mark.asyncio
    async def test_selfheal_kills_subprocess_on_timeout(self, tmp_path, monkeypatch):
        import asyncio as aio
        import sys
        import tinyagentos.llm_proxy as mod

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "tinyagentos"\n')
        (tmp_path / ".local" / "bin").mkdir(parents=True)
        (tmp_path / ".local" / "bin" / "uv").write_text("x")
        monkeypatch.setattr(sys, "executable", str(tmp_path / ".venv" / "bin" / "python"))

        killed = {"called": False}

        class FakeProc:
            returncode = None

            async def communicate(self):
                return (b"", None)

            def kill(self):
                killed["called"] = True

            async def wait(self):
                return 0

        async def fake_exec(*a, **k):
            return FakeProc()

        calls = {"n": 0}

        async def fake_wait_for(aw, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                if hasattr(aw, "close"):
                    aw.close()
                raise aio.TimeoutError()
            return await aw

        monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", fake_exec)
        monkeypatch.setattr(mod.asyncio, "wait_for", fake_wait_for)

        assert await LLMProxy()._selfheal_proxy_extra() is False
        assert killed["called"] is True

    @pytest.mark.asyncio
    async def test_selfheal_locates_root_through_venv_symlink(self, tmp_path, monkeypatch):
        """sys.executable is a venv symlink to the base interpreter; the install
        root must be the venv's grandparent, NOT the symlink target's (regression:
        an earlier .resolve() walked out of the install tree onto /usr)."""
        import sys
        import tinyagentos.llm_proxy as mod

        root = tmp_path / "install"
        (root / ".venv" / "bin").mkdir(parents=True)
        (root / "pyproject.toml").write_text('[project]\nname = "tinyagentos"\n')
        (root / ".local" / "bin").mkdir(parents=True)
        (root / ".local" / "bin" / "uv").write_text("x")
        base = tmp_path / "usr" / "local" / "bin"
        base.mkdir(parents=True)
        real_py = base / "python3"
        real_py.write_text("#!/bin/sh\n")
        venv_py = root / ".venv" / "bin" / "python"
        venv_py.symlink_to(real_py)
        monkeypatch.setattr(sys, "executable", str(venv_py))

        captured = {}

        class FakeProc:
            returncode = 0

            async def communicate(self):
                return (b"", None)

        async def fake_exec(*cmd, **kw):
            captured["cwd"] = kw.get("cwd")
            captured["cmd"] = list(cmd)
            return FakeProc()

        monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", fake_exec)

        ok = await LLMProxy()._selfheal_proxy_extra()
        assert ok is True
        # cwd must be the install root (venv grandparent), not tmp_path/usr.
        assert captured["cwd"] == str(root)

    @pytest.mark.asyncio
    async def test_selfheal_locates_root_at_other_venv_depths(self, tmp_path, monkeypatch):
        """The root walk must not assume <root>/.venv/bin/python exactly: a
        python3.x-named binary or nested layout still finds the first ancestor
        holding pyproject.toml instead of silently no-opping."""
        import sys
        import tinyagentos.llm_proxy as mod

        root = tmp_path / "install"
        deep = root / "envs" / ".venv" / "bin"
        deep.mkdir(parents=True)
        (root / "pyproject.toml").write_text('[project]\nname = "tinyagentos"\n')
        (root / ".local" / "bin").mkdir(parents=True)
        (root / ".local" / "bin" / "uv").write_text("x")
        monkeypatch.setattr(sys, "executable", str(deep / "python3.12"))

        captured = {}

        class FakeProc:
            returncode = 0

            async def communicate(self):
                return (b"", None)

        async def fake_exec(*cmd, **kw):
            captured["cwd"] = kw.get("cwd")
            return FakeProc()

        monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", fake_exec)

        assert await LLMProxy()._selfheal_proxy_extra() is True
        assert captured["cwd"] == str(root)

    @pytest.mark.asyncio
    async def test_selfheal_pip_fallback_installs_only_extra_requirements(
        self, tmp_path, monkeypatch
    ):
        """Without uv, the fallback installs the extras' pinned requirements
        from pyproject, never an editable reinstall of the project: pip
        install -e .[proxy] re-resolves every dependency, the exact churn the
        self-heal exists to undo."""
        import shutil
        import sys
        import tinyagentos.llm_proxy as mod

        (tmp_path / "pyproject.toml").write_text(
            "[project]\nname = \"tinyagentos\"\nversion = \"0\"\n"
            "[project.optional-dependencies]\n"
            # trailing whitespace/newline in an entry must be stripped, not
            # reach pip verbatim
            "proxy = [\"litellm[proxy]>=1.90.0\", \"prisma>=0.11.0\\n\"]\n"
        )
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        monkeypatch.setattr(sys, "executable", str(tmp_path / ".venv" / "bin" / "python"))
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        captured = {}

        class FakeProc:
            returncode = 0

            async def communicate(self):
                return (b"", None)

        async def fake_exec(*cmd, **kw):
            captured["cmd"] = list(cmd)
            return FakeProc()

        monkeypatch.setattr(mod.asyncio, "create_subprocess_exec", fake_exec)

        assert await LLMProxy()._selfheal_proxy_extra() is True
        assert captured["cmd"] == [
            str(tmp_path / ".venv" / "bin" / "pip"),
            "install",
            "--no-input",
            "--disable-pip-version-check",
            "litellm[proxy]>=1.90.0",
            "prisma>=0.11.0",
        ]
        assert "-e" not in captured["cmd"]

    @pytest.mark.asyncio
    async def test_selfheal_ignores_foreign_pyproject(self, tmp_path, monkeypatch):
        """The root walk must not trust just any pyproject.toml above the
        interpreter: a foreign project's file (e.g. one in $HOME) would make
        the self-heal pip-install THAT project's pins into our venv. Only a
        pyproject whose project.name is tinyagentos counts."""
        import sys

        (tmp_path / "pyproject.toml").write_text('[project]\nname = "someone-else"\n')
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        monkeypatch.setattr(sys, "executable", str(tmp_path / ".venv" / "bin" / "python"))

        assert await LLMProxy()._selfheal_proxy_extra() is False


class TestStderrLogHandling:
    """S2-10: the LiteLLM stderr log must stay 0600 across restarts, and the
    parent process must not leak the log's file descriptor."""

    @pytest.mark.asyncio
    async def test_stale_stderr_log_rotated_to_fresh_0600_inode(self, tmp_path, monkeypatch):
        """On start, a stale log (0644 or any permissions) is rotated to .1
        and a fresh 0600 inode opened. A reader holding a descriptor to the
        old inode cannot observe new output."""
        import os as os_mod
        import shutil
        import tinyagentos.llm_proxy as mod

        monkeypatch.setattr(mod, "_pids_listening_on", lambda port: [])
        monkeypatch.setattr(shutil, "which", lambda _: "/fake/litellm")

        class _FakePopen:
            def __init__(self, *a, **kw):
                raise FileNotFoundError("stubbed to skip real spawn")

        monkeypatch.setattr(mod.subprocess, "Popen", _FakePopen)

        p = mod.LLMProxy(port=14017, data_dir=tmp_path)
        config_dir = tmp_path / "litellm"
        config_dir.mkdir(parents=True)
        log_path = config_dir / "litellm.stderr.log"
        stale_content = "stale log from a pre-fix install\n"
        log_path.write_text(stale_content)
        os_mod.chmod(log_path, 0o644)

        # Record the old inode and open a reader fd before start
        old_ino = log_path.stat().st_ino
        rfd = os_mod.open(str(log_path), os_mod.O_RDONLY)

        await p.start(backends=[])

        # After start:
        # (a) log_path has a different inode (rotated away)
        new_ino = log_path.stat().st_ino
        assert new_ino != old_ino, f"Expected different inode after rotation; old={old_ino}, new={new_ino}"

        # (b) new log has mode 0o600
        mode = stat.S_IMODE(log_path.stat().st_mode)
        assert mode == 0o600, f"expected 0600, got {oct(mode)}"

        # (c) .1 exists with mode 0o600 and the stale content
        rotated_path = log_path.with_name("litellm.stderr.log.1")
        assert rotated_path.exists(), f"rotated log {rotated_path} must exist"
        rotated_mode = stat.S_IMODE(rotated_path.stat().st_mode)
        assert rotated_mode == 0o600, f"rotated log expected 0600, got {oct(rotated_mode)}"
        rotated_content = rotated_path.read_text()
        assert rotated_content == stale_content, "rotated log must preserve original content"

        # (d) reader fd still points to old inode and doesn't see new output
        old_fstat_ino = os_mod.fstat(rfd).st_ino
        assert old_fstat_ino == old_ino, "reader fd should still point to old inode"

        # Write a marker to the new log
        log_path.open("a").write("post-restart secret\n")

        # Reader fd should not see this marker (it's on the old inode)
        content_from_old_fd = os_mod.read(rfd, 4096).decode()
        assert "post-restart secret" not in content_from_old_fd, "reader on old inode must not see new output"

        os_mod.close(rfd)

    @pytest.mark.asyncio
    async def test_stderr_log_rotation_keeps_only_one_previous_generation(self, tmp_path, monkeypatch):
        """When .log and .log.1 both exist, rotation replaces .log.1 with the
        old .log, keeping only one previous generation."""
        import os as os_mod
        import shutil
        import tinyagentos.llm_proxy as mod

        monkeypatch.setattr(mod, "_pids_listening_on", lambda port: [])
        monkeypatch.setattr(shutil, "which", lambda _: "/fake/litellm")

        class _FakePopen:
            def __init__(self, *a, **kw):
                raise FileNotFoundError("stubbed to skip real spawn")

        monkeypatch.setattr(mod.subprocess, "Popen", _FakePopen)

        p = mod.LLMProxy(port=14020, data_dir=tmp_path)
        config_dir = tmp_path / "litellm"
        config_dir.mkdir(parents=True)
        log_path = config_dir / "litellm.stderr.log"
        rotated_path = log_path.with_name("litellm.stderr.log.1")

        # Pre-create both .log and .log.1
        current_content = "current log content\n"
        previous_content = "previous log content\n"
        log_path.write_text(current_content)
        rotated_path.write_text(previous_content)

        await p.start(backends=[])

        # After start, .1 must hold the former .log content
        assert rotated_path.read_text() == current_content, ".1 must hold the old .log content"
        # And no .2 should exist
        log_2_path = log_path.with_name("litellm.stderr.log.2")
        assert not log_2_path.exists(), "no .2 generation should exist"

    @pytest.mark.asyncio
    async def test_stderr_handle_closed_after_successful_start(self, tmp_path, monkeypatch):
        """The parent keeps stdio piped to the child via the inherited fd;
        it must close its own copy right after Popen() succeeds, or a
        repeated start() leaks one descriptor per attempt."""
        import os as os_mod
        import shutil
        import tinyagentos.llm_proxy as mod

        class _FakeResp:
            status_code = 200

        class _FakeClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *exc): return False
            async def get(self, url): return _FakeResp()

        monkeypatch.setattr(mod.httpx, "AsyncClient", _FakeClient)
        monkeypatch.setattr(mod, "_pids_listening_on", lambda port: [])
        monkeypatch.setattr(shutil, "which", lambda _: "/fake/litellm")

        class _FakePopen:
            def __init__(self, *a, **kw):
                pass

        monkeypatch.setattr(mod.subprocess, "Popen", _FakePopen)

        captured_handles = []
        orig_fdopen = os_mod.fdopen

        def _tracking_fdopen(fd, *a, **kw):
            handle = orig_fdopen(fd, *a, **kw)
            captured_handles.append(handle)
            return handle

        monkeypatch.setattr(mod.os, "fdopen", _tracking_fdopen)

        p = mod.LLMProxy(port=14018, data_dir=tmp_path)
        result = await p.start(backends=[])

        assert result is True
        assert len(captured_handles) == 1
        assert captured_handles[0].closed, "parent must close its stderr log handle"

    @pytest.mark.asyncio
    async def test_stderr_handle_closed_when_popen_raises(self, tmp_path, monkeypatch):
        """Same cleanup is required on the failed-spawn path (litellm binary
        missing) — the handle must not leak just because Popen() failed."""
        import os as os_mod
        import shutil
        import tinyagentos.llm_proxy as mod

        monkeypatch.setattr(mod, "_pids_listening_on", lambda port: [])
        monkeypatch.setattr(shutil, "which", lambda _: "/fake/litellm")

        class _FakePopen:
            def __init__(self, *a, **kw):
                raise FileNotFoundError("stubbed")

        monkeypatch.setattr(mod.subprocess, "Popen", _FakePopen)

        captured_handles = []
        orig_fdopen = os_mod.fdopen

        def _tracking_fdopen(fd, *a, **kw):
            handle = orig_fdopen(fd, *a, **kw)
            captured_handles.append(handle)
            return handle

        monkeypatch.setattr(mod.os, "fdopen", _tracking_fdopen)

        p = mod.LLMProxy(port=14019, data_dir=tmp_path)
        result = await p.start(backends=[])

        assert result is False
        assert len(captured_handles) == 1
        assert captured_handles[0].closed, "parent must close its stderr log handle even on failed spawn"


class TestConfigDirPermissions:
    """S2-10: LiteLLM config dir and files must not be world-readable.

    The master key, backend API keys, and callback shim .py files must live
    under <data_dir>/litellm (not /tmp), with the directory at 0700 and every
    file at 0600.
    """

    @pytest.mark.asyncio
    async def test_config_dir_under_data_dir(self, tmp_path):
        """When data_dir is set, config_dir must be <data_dir>/litellm, not /tmp."""
        proxy = LLMProxy(port=14010, data_dir=tmp_path)
        assert proxy.config_dir == tmp_path / "litellm"

    @pytest.mark.asyncio
    async def test_config_dir_not_tmp_default(self, tmp_path):
        """With data_dir set, config_dir must not be the old /tmp/taos-litellm default."""
        proxy = LLMProxy(port=14011, data_dir=tmp_path)
        assert str(proxy.config_dir) != "/tmp/taos-litellm"
        assert not str(proxy.config_dir).startswith("/tmp/taos-litellm")

    @pytest.mark.asyncio
    async def test_config_dir_default_still_tmp_when_no_data_dir(self):
        """Without data_dir, the legacy /tmp fallback is retained (e.g. ad-hoc tests)."""
        proxy = LLMProxy(port=14012)
        assert proxy.config_dir == Path("/tmp/taos-litellm")

    @pytest.mark.asyncio
    async def test_config_dir_mode_0700(self, tmp_path):
        """Config directory must be created with mode 0700."""
        proxy = LLMProxy(port=14013, data_dir=tmp_path)
        await proxy.write_config([])
        mode = stat.S_IMODE(proxy.config_dir.stat().st_mode)
        assert mode == 0o700

    @pytest.mark.asyncio
    async def test_config_files_mode_0600(self, tmp_path):
        """Every file written by write_config must have mode 0600."""
        proxy = LLMProxy(port=14014, data_dir=tmp_path, inhouse_keys=True)
        await proxy.write_config([])
        expected_files = ["litellm_config.yaml", "taos_callback.py", "taos_auth.py"]
        for name in expected_files:
            f = proxy.config_dir / name
            assert f.exists(), f"{name} was not written"
            mode = stat.S_IMODE(f.stat().st_mode)
            assert mode == 0o600, f"{name} has mode {oct(mode)}, expected 0o600"

    @pytest.mark.asyncio
    async def test_config_contents_include_master_key(self, tmp_path):
        """The master key is embedded in the generated yaml (regression guard for
        the permission fix — the key must still be present, just unreadable by others)."""
        key = get_litellm_master_key(tmp_path)
        proxy = LLMProxy(port=14015, data_dir=tmp_path)
        await proxy.write_config([])
        import yaml
        yaml_text = (proxy.config_dir / "litellm_config.yaml").read_text()
        assert key in yaml_text

    @pytest.mark.asyncio
    async def test_write_config_fails_closed_when_chmod_fails(self, tmp_path):
        """If hardening the config dir to 0700 fails, write_config must raise
        BEFORE writing any generated file — otherwise a local user who
        controls the (still-insecure) directory could plant or read the
        config/shims before LiteLLM ever loads them."""
        proxy = LLMProxy(port=14016, data_dir=tmp_path)
        with patch("os.chmod", side_effect=OSError("boom")):
            with pytest.raises(PermissionError):
                await proxy.write_config([])
        assert not (proxy.config_dir / "litellm_config.yaml").exists()
        assert not (proxy.config_dir / "taos_callback.py").exists()


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
