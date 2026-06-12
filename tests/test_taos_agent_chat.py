"""Tests for the rewired POST /api/taos-agent/chat endpoint (opencode backend).

Uses the same client/app fixtures as the rest of the route tests.
Adapters and ensure_taos_opencode_server are monkeypatched so no real
opencode server or LiteLLM proxy is needed.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_data_dir(tmp_path):
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [
            {"name": "test-backend", "type": "rkllama", "url": "http://localhost:8080", "priority": 1}
        ],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    return tmp_path


@pytest.fixture
def app(tmp_data_dir):
    return create_app(data_dir=tmp_data_dir)


@pytest_asyncio.fixture
async def client(app):
    ds = app.state.desktop_settings
    if ds._db is not None:
        await ds.close()
    await ds.init()
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": token},
    ) as c:
        yield c
    await ds.close()
    await app.state.http_client.aclose()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_server(base_url: str = "http://127.0.0.1:4188"):
    """A minimal stand-in for OpenCodeServer that has a base_url."""
    s = SimpleNamespace()
    s.base_url = base_url
    return s


def _make_mock_proxy(running: bool = True):
    proxy = MagicMock()
    proxy.is_running.return_value = running
    return proxy


def _parse_ndjson(text: str) -> list[dict]:
    """Parse all non-empty NDJSON lines from a response body."""
    items = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
    return items


# ---------------------------------------------------------------------------
# Guard tests (400 / 503 before opencode is touched)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_no_model_returns_400(client):
    """POST /api/taos-agent/chat with no model configured → 400."""
    resp = await client.post(
        "/api/taos-agent/chat",
        json={"messages": [{"role": "user", "content": "Hello"}]},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert "model" in data.get("error", "").lower()


@pytest.mark.asyncio
async def test_chat_proxy_not_running_returns_503(client, app):
    """POST /api/taos-agent/chat when proxy not running → 503."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})
    app.state.llm_proxy = _make_mock_proxy(running=False)

    resp = await client.post(
        "/api/taos-agent/chat",
        json={"messages": [{"role": "user", "content": "Hello"}]},
    )
    assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Happy-path: two deltas then final → delta, delta, done
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_happy_path_ndjson(client, app, monkeypatch):
    """Two delta replies followed by a final reply → {delta}, {delta}, {done}."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})
    app.state.llm_proxy = _make_mock_proxy(running=True)
    app.state.taos_opencode_password = "testpw"
    app.state.taos_opencode_session_id = None

    server = _fake_server()

    async def fake_ensure_server(state, model):
        return server

    monkeypatch.setattr(
        "tinyagentos.routes.taos_agent.ensure_taos_opencode_server",
        fake_ensure_server,
    )

    class _FakeAdapter:
        def __init__(self, cfg, sink):
            self._sink = sink
            self.session_id = None

        async def ensure_session(self):
            self.session_id = "ses_happy"

        async def prompt(self, text, trace_id=None, attachments=None):
            self._sink({"kind": "delta", "content": "Hello"})
            self._sink({"kind": "delta", "content": " world"})
            self._sink({"kind": "final", "content": "Hello world"})

        async def close(self):
            pass

    monkeypatch.setattr(
        "tinyagentos.routes.taos_agent.OpenCodeAdapter",
        _FakeAdapter,
    )

    resp = await client.post(
        "/api/taos-agent/chat",
        json={"messages": [{"role": "user", "content": "Hi"}]},
    )
    assert resp.status_code == 200
    assert "application/x-ndjson" in resp.headers.get("content-type", "")

    items = _parse_ndjson(resp.text)
    delta_items = [i for i in items if "delta" in i]
    assert len(delta_items) == 2
    assert delta_items[0]["delta"] == "Hello"
    assert delta_items[1]["delta"] == " world"
    done_items = [i for i in items if i.get("done") is True]
    assert len(done_items) == 1
    # done must be the last item
    assert items[-1] == {"done": True}


# ---------------------------------------------------------------------------
# Error path: adapter emits error → {error}, {done}
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_error_path_ndjson(client, app, monkeypatch):
    """Adapter emits an error reply → {error:...}, {done:true}."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})
    app.state.llm_proxy = _make_mock_proxy(running=True)
    app.state.taos_opencode_password = "testpw"
    app.state.taos_opencode_session_id = None

    server = _fake_server()

    async def fake_ensure_server(state, model):
        return server

    monkeypatch.setattr(
        "tinyagentos.routes.taos_agent.ensure_taos_opencode_server",
        fake_ensure_server,
    )

    class _ErrorAdapter:
        def __init__(self, cfg, sink):
            self._sink = sink
            self.session_id = None

        async def ensure_session(self):
            self.session_id = "ses_err"

        async def prompt(self, text, trace_id=None, attachments=None):
            self._sink({"kind": "error", "error": "boom"})

        async def close(self):
            pass

    monkeypatch.setattr(
        "tinyagentos.routes.taos_agent.OpenCodeAdapter",
        _ErrorAdapter,
    )

    resp = await client.post(
        "/api/taos-agent/chat",
        json={"messages": [{"role": "user", "content": "Hi"}]},
    )
    assert resp.status_code == 200
    items = _parse_ndjson(resp.text)
    error_items = [i for i in items if "error" in i]
    assert len(error_items) >= 1
    assert error_items[0]["error"] == "boom"
    assert items[-1] == {"done": True}


# ---------------------------------------------------------------------------
# ensure_taos_opencode_server: key minting and master-key fallback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_server_uses_agent_key_when_available(tmp_path, monkeypatch):
    """When create_agent_key returns a key, the server config uses that key."""
    import tinyagentos.taos_agent_runtime as rt

    spawned_cfgs: list = []

    class _FakeServer:
        def __init__(self, cfg):
            spawned_cfgs.append(cfg)
            self._cfg = cfg

        async def ensure_running(self, **kwargs):
            pass

        async def stop(self):
            pass

        @property
        def base_url(self):
            return f"http://127.0.0.1:{self._cfg.port}"

        def is_running(self):
            return True

    monkeypatch.setattr(rt, "OpenCodeServer", _FakeServer)

    mock_proxy = MagicMock()
    mock_proxy.create_agent_key = AsyncMock(return_value="sk-agent-key-123")
    mock_proxy.is_running.return_value = True

    state = SimpleNamespace(
        data_dir=tmp_path,
        llm_proxy=mock_proxy,
        taos_opencode_password=None,
        taos_opencode_server=None,
        taos_opencode_model=None,
        taos_opencode_session_id=None,
    )

    await rt.ensure_taos_opencode_server(state, "gpt-4o")

    assert len(spawned_cfgs) == 1
    assert spawned_cfgs[0].litellm_key == "sk-agent-key-123"
    assert state.taos_opencode_key == "sk-agent-key-123"


@pytest.mark.asyncio
async def test_ensure_server_falls_back_to_master_key(tmp_path, monkeypatch):
    """When create_agent_key returns None, the server falls back to the master key."""
    import tinyagentos.taos_agent_runtime as rt
    from tinyagentos.litellm_config import get_litellm_master_key
    expected_master_key = get_litellm_master_key(tmp_path)

    spawned_cfgs: list = []

    class _FakeServer:
        def __init__(self, cfg):
            spawned_cfgs.append(cfg)
            self._cfg = cfg

        async def ensure_running(self, **kwargs):
            pass

        @property
        def base_url(self):
            return f"http://127.0.0.1:{self._cfg.port}"

        def is_running(self):
            return True

    monkeypatch.setattr(rt, "OpenCodeServer", _FakeServer)

    mock_proxy = MagicMock()
    mock_proxy.create_agent_key = AsyncMock(return_value=None)
    mock_proxy.is_running.return_value = True

    state = SimpleNamespace(
        data_dir=tmp_path,
        llm_proxy=mock_proxy,
        taos_opencode_password=None,
        taos_opencode_server=None,
        taos_opencode_model=None,
        taos_opencode_session_id=None,
    )

    await rt.ensure_taos_opencode_server(state, "gpt-4o")

    assert len(spawned_cfgs) == 1
    assert spawned_cfgs[0].litellm_key == expected_master_key
    assert state.taos_opencode_key == expected_master_key


@pytest.mark.asyncio
async def test_ensure_server_reuses_persisted_key(tmp_path, monkeypatch):
    """A persisted own-key is reused and re-scoped, never re-minted (the fixed
    key alias would otherwise 400 on the second mint)."""
    import tinyagentos.taos_agent_runtime as rt

    spawned_cfgs: list = []

    class _FakeServer:
        def __init__(self, cfg):
            spawned_cfgs.append(cfg)
            self._cfg = cfg
        async def ensure_running(self, **kwargs):
            pass
        async def stop(self):
            pass
        @property
        def base_url(self):
            return f"http://127.0.0.1:{self._cfg.port}"
        def is_running(self):
            return True

    monkeypatch.setattr(rt, "OpenCodeServer", _FakeServer)

    class _FakeSettings:
        async def get_preference(self, user, ns):
            return {"llm_key": "sk-persisted-9", "permitted_models": ["gpt-4o", "claude"]}
        async def save_preference(self, user, ns, prefs):
            pass

    mock_proxy = MagicMock()
    mock_proxy.create_agent_key = AsyncMock(return_value="sk-NEW-should-not-be-used")
    mock_proxy.update_agent_key = AsyncMock(return_value=True)
    mock_proxy.is_running.return_value = True

    state = SimpleNamespace(
        data_dir=tmp_path,
        llm_proxy=mock_proxy,
        desktop_settings=_FakeSettings(),
        taos_opencode_password=None,
        taos_opencode_server=None,
        taos_opencode_model=None,
        taos_opencode_session_id=None,
    )

    await rt.ensure_taos_opencode_server(state, "gpt-4o")

    # Reused the persisted key, did NOT re-mint, and re-scoped it to the set.
    assert spawned_cfgs[0].litellm_key == "sk-persisted-9"
    assert state.taos_opencode_key == "sk-persisted-9"
    mock_proxy.create_agent_key.assert_not_called()
    mock_proxy.update_agent_key.assert_awaited()


# ---------------------------------------------------------------------------
# Degraded-birth detection and self-heal
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_server_born_degraded_when_proxy_not_running(tmp_path, monkeypatch):
    """Server built while proxy not running sets the born_degraded flag."""
    import tinyagentos.taos_agent_runtime as rt

    class _FakeServer:
        def __init__(self, cfg):
            self._cfg = cfg

        async def ensure_running(self, **kwargs):
            pass

        async def stop(self):
            pass

        @property
        def base_url(self):
            return f"http://127.0.0.1:{self._cfg.port}"

        def is_running(self):
            return False

    monkeypatch.setattr(rt, "OpenCodeServer", _FakeServer)

    mock_proxy = MagicMock()
    mock_proxy.is_running.return_value = False
    mock_proxy.create_agent_key = AsyncMock(return_value=None)

    state = SimpleNamespace(
        data_dir=tmp_path,
        llm_proxy=mock_proxy,
        taos_opencode_password=None,
        taos_opencode_server=None,
        taos_opencode_model=None,
        taos_opencode_session_id=None,
    )

    await rt.ensure_taos_opencode_server(state, "gpt-4o")

    assert state.taos_opencode_born_degraded is True


@pytest.mark.asyncio
async def test_ensure_server_self_heals_when_proxy_becomes_ready(tmp_path, monkeypatch):
    """Second ensure call with proxy now running rebuilds: old server stopped, new one created, flag cleared."""
    import tinyagentos.taos_agent_runtime as rt

    stop_calls: list[str] = []
    spawned_cfgs: list = []

    class _FakeServer:
        def __init__(self, cfg):
            spawned_cfgs.append(cfg)
            self._cfg = cfg

        async def ensure_running(self, **kwargs):
            pass

        async def stop(self):
            stop_calls.append("stopped")

        @property
        def base_url(self):
            return f"http://127.0.0.1:{self._cfg.port}"

        def is_running(self):
            return True

    monkeypatch.setattr(rt, "OpenCodeServer", _FakeServer)

    mock_proxy = MagicMock()
    mock_proxy.is_running.return_value = False
    mock_proxy.create_agent_key = AsyncMock(return_value="sk-key-1")

    state = SimpleNamespace(
        data_dir=tmp_path,
        llm_proxy=mock_proxy,
        taos_opencode_password=None,
        taos_opencode_server=None,
        taos_opencode_model=None,
        taos_opencode_session_id=None,
    )

    # First call: proxy not ready, server born degraded.
    await rt.ensure_taos_opencode_server(state, "gpt-4o")
    assert state.taos_opencode_born_degraded is True
    assert len(spawned_cfgs) == 1

    # Proxy comes up.
    mock_proxy.is_running.return_value = True

    # Second call: proxy is ready now, so should tear down and rebuild.
    await rt.ensure_taos_opencode_server(state, "gpt-4o")

    assert len(stop_calls) == 1, "old server must have been stopped"
    assert len(spawned_cfgs) == 2, "a new server must have been created"
    assert state.taos_opencode_born_degraded is False


# ---------------------------------------------------------------------------
# Silent-stream guard
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_empty_stream_yields_error_frame(client, app, monkeypatch):
    """When the runtime stream is empty (only done), an error frame is emitted."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})
    app.state.llm_proxy = _make_mock_proxy(running=True)
    app.state.taos_opencode_password = "testpw"
    app.state.taos_opencode_session_id = None

    server = _fake_server()

    async def fake_ensure_server(state, model):
        return server

    monkeypatch.setattr(
        "tinyagentos.routes.taos_agent.ensure_taos_opencode_server",
        fake_ensure_server,
    )

    class _EmptyAdapter:
        def __init__(self, cfg, sink):
            self._sink = sink
            self.session_id = None

        async def ensure_session(self):
            self.session_id = "ses_empty"

        async def prompt(self, text, trace_id=None, attachments=None):
            # Emit only final with no deltas — simulates degraded opencode.
            self._sink({"kind": "final", "content": ""})

        async def close(self):
            pass

    monkeypatch.setattr(
        "tinyagentos.routes.taos_agent.OpenCodeAdapter",
        _EmptyAdapter,
    )

    resp = await client.post(
        "/api/taos-agent/chat",
        json={"messages": [{"role": "user", "content": "Hi"}]},
    )
    assert resp.status_code == 200
    items = _parse_ndjson(resp.text)
    error_items = [i for i in items if "error" in i]
    assert len(error_items) >= 1
    assert "warming" in error_items[0]["error"] or "proxy" in error_items[0]["error"]
    assert items[-1] == {"done": True}


@pytest.mark.asyncio
async def test_chat_normal_stream_no_spurious_error(client, app, monkeypatch):
    """A normal stream with deltas must NOT emit the empty-stream error frame."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})
    app.state.llm_proxy = _make_mock_proxy(running=True)
    app.state.taos_opencode_password = "testpw"
    app.state.taos_opencode_session_id = None

    server = _fake_server()

    async def fake_ensure_server(state, model):
        return server

    monkeypatch.setattr(
        "tinyagentos.routes.taos_agent.ensure_taos_opencode_server",
        fake_ensure_server,
    )

    class _NormalAdapter:
        def __init__(self, cfg, sink):
            self._sink = sink
            self.session_id = None

        async def ensure_session(self):
            self.session_id = "ses_normal"

        async def prompt(self, text, trace_id=None, attachments=None):
            self._sink({"kind": "delta", "content": "pong"})
            self._sink({"kind": "final", "content": "pong"})

        async def close(self):
            pass

    monkeypatch.setattr(
        "tinyagentos.routes.taos_agent.OpenCodeAdapter",
        _NormalAdapter,
    )

    resp = await client.post(
        "/api/taos-agent/chat",
        json={"messages": [{"role": "user", "content": "Hi"}]},
    )
    assert resp.status_code == 200
    items = _parse_ndjson(resp.text)
    error_items = [i for i in items if "error" in i]
    assert len(error_items) == 0
    delta_items = [i for i in items if "delta" in i]
    assert len(delta_items) == 1
    assert delta_items[0]["delta"] == "pong"
    assert items[-1] == {"done": True}
