from unittest.mock import patch

import pytest
from tinyagentos.llm_proxy import (
    EMBEDDING_ALIAS,
    TAOS_LITELLM_MASTER_KEY,
    _is_embedding_model,
    generate_litellm_config,
    LLMProxy,
)


class TestConfigGeneration:
    def test_generates_config_from_backends(self):
        backends = [
            {"name": "fedora-gpu", "type": "ollama", "url": "http://fedora:11434", "priority": 1},
            {"name": "local-npu", "type": "rkllama", "url": "http://localhost:8080", "priority": 3},
        ]
        config = generate_litellm_config(backends)
        assert "model_list" in config
        assert len(config["model_list"]) >= 2
        # First entry should be highest priority
        assert config["model_list"][0]["litellm_params"]["api_base"] == "http://fedora:11434"

    def test_empty_backends_returns_empty_model_list(self):
        config = generate_litellm_config([])
        assert config["model_list"] == []

    def test_config_emits_master_key(self):
        """general_settings.master_key must carry the shared taOS master
        key so LiteLLM rejects unauthenticated requests and accepts the
        value the deployer injects into every agent container."""
        config = generate_litellm_config([])
        assert config["general_settings"]["master_key"] == "sk-taos-master"
        assert config["general_settings"]["master_key"] == TAOS_LITELLM_MASTER_KEY

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
            "tinyagentos.llm_proxy._discover_ollama_models",
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
        with patch("tinyagentos.llm_proxy._discover_ollama_models", return_value=[]):
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

        with patch("tinyagentos.llm_proxy._discover_ollama_models", side_effect=_fake_probe):
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
        with caplog.at_level(logging.WARNING, logger="tinyagentos.llm_proxy"):
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
        with caplog.at_level(logging.WARNING, logger="tinyagentos.llm_proxy"):
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

    def test_write_config_creates_callback_shim(self, tmp_path):
        """``write_config`` writes a sibling ``taos_callback.py`` next to
        the generated yaml, re-exporting the installed callback instance as
        ``proxy_handler_instance`` — so LiteLLM's config-dir-relative import
        succeeds without duplicating the callback source."""
        proxy = LLMProxy(port=14000, config_dir=tmp_path)
        proxy.write_config([])
        shim = tmp_path / "taos_callback.py"
        assert shim.exists()
        contents = shim.read_text()
        assert (
            "from tinyagentos.litellm_callback import taos_callback "
            "as proxy_handler_instance"
        ) in contents


class TestLLMProxy:
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
        assert captured["env"]["LITELLM_MASTER_KEY"] == "sk-taos-master"

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
        monkeypatch.setattr(mod, "_discover_ollama_models", lambda *a, **kw: [])

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
