"""RED tests for Q2-1 security hygiene sweep (tsk-wc5fns).

Covers:
  - litellm_auth: secrets.compare_digest for master-key comparison
  - routes/agents.py: secrets.compare_digest for llm_key bearer match
  - opencode_runtime: atomic_write_text(mode=0o600) for config; log file created
    with mode 0o600 at open-time (no separate chmod)
  - torrent_downloader: _params_from_torrent_url is async (no sync httpx on loop)
  - knowledge_store: update_item rejects unknown columns; search_fts LIKE
    fallback escapes % and _
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
import types
from pathlib import Path

import pytest

import pytest_asyncio
from tinyagentos.knowledge_store import KnowledgeStore


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def knowledge_store(tmp_path):
    s = KnowledgeStore(tmp_path / "knowledge.db", media_dir=tmp_path / "knowledge-media")
    await s.init()
    yield s
    await s.close()


async def _add_item(store, title="T", content="C", summary="S", author="A"):
    return await store.add_item(
        source_type="article",
        source_url="https://example.com/1",
        title=title,
        author=author,
        content=content,
        summary=summary,
        categories=[],
        tags=[],
        metadata={},
    )


# ---------------------------------------------------------------------------
# 1. litellm_auth -- secrets.compare_digest for master key
# ---------------------------------------------------------------------------


class _FakeRequest:
    """Minimal request stand-in for _requested_model."""
    def __init__(self, body: dict | None = None):
        self._body = body or {}

    async def json(self):
        return self._body


def _stub_litellm(monkeypatch):
    """Inject a minimal litellm.proxy._types.UserAPIKeyAuth into sys.modules."""
    class _UserAPIKeyAuth:
        def __init__(self, **kw):
            self.api_key = kw.get("api_key")
            self.key_alias = kw.get("key_alias")
            self.models = kw.get("models", [])
            self.metadata = kw.get("metadata", {})

    litellm = types.ModuleType("litellm")
    proxy = types.ModuleType("litellm.proxy")
    _types = types.ModuleType("litellm.proxy._types")
    _types.UserAPIKeyAuth = _UserAPIKeyAuth
    proxy._types = _types
    litellm.proxy = proxy
    monkeypatch.setitem(sys.modules, "litellm", litellm)
    monkeypatch.setitem(sys.modules, "litellm.proxy", proxy)
    monkeypatch.setitem(sys.modules, "litellm.proxy._types", _types)


@pytest.mark.asyncio
async def test_litellm_auth_master_key_uses_compare_digest(monkeypatch):
    """Master-key comparison must go through secrets.compare_digest."""
    import secrets as secrets_mod

    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-master-123")
    monkeypatch.delenv("TAOS_LITELLM_KEYSTORE", raising=False)
    monkeypatch.delenv("TAOS_AGENT_BUDGETS", raising=False)

    _stub_litellm(monkeypatch)

    called = []
    real = secrets_mod.compare_digest
    def spy(a, b):
        called.append((a, b))
        return real(a, b)
    monkeypatch.setattr(secrets_mod, "compare_digest", spy)

    import tinyagentos.litellm_auth as auth
    importlib.reload(auth)

    result = await auth.user_api_key_auth(_FakeRequest(), "sk-master-123")
    assert result is not None
    assert called, "secrets.compare_digest was not called for master-key check"
    assert called[0][0] == "sk-master-123"
    assert called[0][1] == "sk-master-123"


# ---------------------------------------------------------------------------
# 2. routes/agents.py -- secrets.compare_digest for bearer token match
# ---------------------------------------------------------------------------


class _FakeConfig:
    def __init__(self, agents):
        self.agents = agents
        self.config_path = "/dev/null"
        self.backends = []


class _FakeState:
    def __init__(self, config):
        self.config = config


class _FakeApp:
    def __init__(self, state):
        self.state = state


class _FakeBearerRequest:
    """Request stub whose .headers behaves like a dict for .get()."""
    def __init__(self, agents, token):
        state = _FakeState(_FakeConfig(agents))
        self.app = _FakeApp(state)
        self.headers = {"authorization": f"Bearer {token}"}


def test_resolve_agent_by_bearer_uses_compare_digest(monkeypatch):
    """Bearer-token match must use secrets.compare_digest, not ==."""
    import secrets as secrets_mod

    called = []
    real = secrets_mod.compare_digest
    def spy(a, b):
        called.append((a, b))
        return real(a, b)
    monkeypatch.setattr(secrets_mod, "compare_digest", spy)

    from tinyagentos.routes.agents import _resolve_agent_by_bearer

    agents = [
        {"name": "alpha", "llm_key": "sk-agent-secret", "model": "gpt-4o"},
        {"name": "beta", "llm_key": "sk-other-key", "model": "gpt-4o"},
    ]
    request = _FakeBearerRequest(agents, "sk-agent-secret")
    agent = _resolve_agent_by_bearer(request)
    assert agent is not None
    assert agent["name"] == "alpha"
    assert called, "secrets.compare_digest was not called for bearer match"
    assert called[0][0] == "sk-agent-secret"
    assert called[0][1] == "sk-agent-secret"


# ---------------------------------------------------------------------------
# 3. opencode_runtime -- write_config uses atomic_write_text(mode=0o600)
# ---------------------------------------------------------------------------


def _make_server_cfg(tmp_path, port=5900):
    from tinyagentos.opencode_runtime import OpenCodeServerConfig
    return OpenCodeServerConfig(
        home=str(tmp_path),
        port=port,
        server_password=None,
        litellm_base_url="http://127.0.0.1:4000/v1",
        litellm_key="sk-test",
        model_ids=["gpt-4o", "gpt-3.5-turbo"],
    )


def test_write_config_uses_atomic_write_text(tmp_path, monkeypatch):
    """write_config must go through atomic_write_text with mode=0o600."""
    import tinyagentos.opencode_runtime as ocr_mod
    from tinyagentos.atomic_io import atomic_write_text as real_awt

    calls = []

    def spy(path, text, **kwargs):
        calls.append((str(path), text, kwargs))
        real_awt(path, text, **kwargs)

    monkeypatch.setattr(ocr_mod, "atomic_write_text", spy, raising=False)

    cfg = _make_server_cfg(tmp_path)
    ocr_mod.OpenCodeServer(cfg).write_config()

    assert calls, "atomic_write_text was not called by write_config"
    assert calls[0][2].get("mode") == 0o600, \
        f"expected mode=0o600, got {calls[0][2]}"


# ---------------------------------------------------------------------------
# 4. opencode_runtime -- serve.log created with mode 0o600 (no separate chmod)
# ---------------------------------------------------------------------------


class _FakeProcess:
    def __init__(self):
        self.returncode = None
        self.pid = 12345

    async def wait(self):
        pass


@pytest.mark.asyncio
async def test_ensure_running_log_file_not_chmod_separately(tmp_path, monkeypatch):
    """serve.log must be created with mode 0600 at open-time, not chmod'd."""
    from tinyagentos.opencode_runtime import OpenCodeServer

    cfg = _make_server_cfg(tmp_path)
    server = OpenCodeServer(cfg)

    chmod_targets = []
    real_chmod = os.chmod

    def spy_chmod(path, mode):
        chmod_targets.append(str(path))
        return real_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", spy_chmod)

    async def fake_create_subprocess(*args, **kwargs):
        return _FakeProcess()

    async def fake_health(self_inner):
        return True

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess)
    monkeypatch.setattr(OpenCodeServer, "_health_check", fake_health)

    await server.ensure_running(poll_s=0.0)

    log_path = str(tmp_path / ".config" / "opencode" / "serve.log")
    assert log_path not in chmod_targets, \
        "serve.log should be created with mode at open-time, not chmod'd separately"
    assert os.stat(log_path).st_mode & 0o777 == 0o600, \
        f"expected 0o600, got {oct(os.stat(log_path).st_mode & 0o777)}"


# ---------------------------------------------------------------------------
# 5. torrent_downloader -- _params_from_torrent_url is async
# ---------------------------------------------------------------------------


def test_params_from_torrent_url_is_async():
    """_params_from_torrent_url must be async so it never blocks the event loop."""
    from tinyagentos.torrent_downloader import TorrentDownloader
    assert asyncio.iscoroutinefunction(
        TorrentDownloader._params_from_torrent_url
    ), "_params_from_torrent_url must be async (was sync httpx.get on the loop)"


# ---------------------------------------------------------------------------
# 6. knowledge_store -- update_item rejects unknown columns
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_item_rejects_unknown_column(knowledge_store):
    """update_item must reject column names not in the allowlist."""
    item_id = await _add_item(knowledge_store)
    with pytest.raises(ValueError, match="unknown column"):
        await knowledge_store.update_item(item_id, evil_column="data")


# ---------------------------------------------------------------------------
# 7. knowledge_store -- LIKE fallback escapes % and _
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_fts_like_escapes_wildcards(knowledge_store):
    """LIKE fallback must escape % and _ so they match literally."""
    await _add_item(knowledge_store, title="100% Complete", content="c1")
    await _add_item(knowledge_store, title="100 dollars", content="c2")

    # Force the FTS MATCH path to fail so the LIKE fallback is exercised.
    real_execute = knowledge_store._db.execute

    async def failing_on_fts(sql, *args, **kwargs):
        if "MATCH" in sql:
            raise RuntimeError("forced FTS failure")
        return await real_execute(sql, *args, **kwargs)

    knowledge_store._db.execute = failing_on_fts

    results = await knowledge_store.search_fts("100%")
    # Before fix: LIKE '%100%%' treats % as wildcard, matching both rows.
    # After fix:  LIKE '%100\%%' ESCAPE '\' matches only "100% Complete".
    assert len(results) == 1
    assert results[0]["title"] == "100% Complete"
