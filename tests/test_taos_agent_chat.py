"""Tests for the rewired POST /api/taos-agent/chat endpoint (opencode backend).

Uses the same client/app fixtures as the rest of the route tests.
Adapters and ensure_taos_opencode_server are monkeypatched so no real
opencode server or LiteLLM proxy is needed.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
import yaml
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app
from taos_test_csrf import csrf_event_hooks


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
        event_hooks=csrf_event_hooks(),
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
# #1616 — opencode-not-found vs opencode-found-but-failed-to-start must
# return distinct, accurate errors (the old code masked both behind a single
# generic "check that opencode is installed" message).
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_opencode_binary_not_found_returns_install_instructions(client, app, monkeypatch):
    """When opencode genuinely isn't installed anywhere, the error tells the
    user to install it -- not a vague "unavailable" message."""
    from tinyagentos.opencode_runtime import OpenCodeBinaryNotFoundError

    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})
    app.state.llm_proxy = _make_mock_proxy(running=True)

    async def fake_ensure_server(state, model):
        raise OpenCodeBinaryNotFoundError("opencode binary not found (tried 'opencode')")

    monkeypatch.setattr(
        "tinyagentos.routes.taos_agent.ensure_taos_opencode_server",
        fake_ensure_server,
    )

    resp = await client.post(
        "/api/taos-agent/chat",
        json={"messages": [{"role": "user", "content": "Hello"}]},
    )
    assert resp.status_code == 503
    error = resp.json().get("error", "")
    assert "install" in error.lower()
    assert "opencode.ai/install" in error


@pytest.mark.asyncio
async def test_chat_opencode_start_failure_surfaces_real_error(client, app, monkeypatch):
    """When opencode is found but fails to start, the real failure reason is
    surfaced -- not masked behind the same generic message as a missing
    binary. This must be text distinguishable from the not-found case."""
    await client.patch("/api/taos-agent/settings", json={"model": "gpt-4o"})
    app.state.llm_proxy = _make_mock_proxy(running=True)

    async def fake_ensure_server(state, model):
        raise TimeoutError("opencode server on port 4188 did not become healthy within 180.0s")

    monkeypatch.setattr(
        "tinyagentos.routes.taos_agent.ensure_taos_opencode_server",
        fake_ensure_server,
    )

    resp = await client.post(
        "/api/taos-agent/chat",
        json={"messages": [{"role": "user", "content": "Hello"}]},
    )
    assert resp.status_code == 503
    error = resp.json().get("error", "")
    # The real failure reason must be present, not swallowed.
    assert "did not become healthy" in error
    assert "install" not in error.lower()


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


@pytest.mark.asyncio
async def test_chat_queued_message_survives_turn_error(client, app, monkeypatch):
    """A message queued mid-turn is surfaced even when the turn ERRORS.

    The sink's error path enqueues _DONE early, which used to strand the
    queued-message frames _drive's finally emits behind it in a queue nobody
    read - the queued user's message silently evaporated on the most common
    failure (a model-proxy error). The generator now drains the settled
    queue after the early _DONE.
    """
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

    class _QueueThenErrorAdapter:
        def __init__(self, cfg, sink):
            self._sink = sink
            self.session_id = None

        async def ensure_session(self):
            self.session_id = "ses_qerr"

        async def prompt(self, text, trace_id=None, attachments=None):
            # A second user message lands while this turn is in flight...
            loop = app.state.taos_agent_loop
            from tinyagentos.agent_loop import LoopAction
            action = await loop.handle_message("urgent follow-up", msg_id="q1")
            assert action is LoopAction.QUEUED
            # ...and then the turn fails (the common model-proxy shape).
            self._sink({"kind": "error", "error": "proxy exploded"})

        async def close(self):
            pass

    monkeypatch.setattr(
        "tinyagentos.routes.taos_agent.OpenCodeAdapter",
        _QueueThenErrorAdapter,
    )

    resp = await client.post(
        "/api/taos-agent/chat",
        json={"messages": [{"role": "user", "content": "Hi"}]},
    )
    assert resp.status_code == 200
    assert "urgent follow-up" in resp.text
    items = _parse_ndjson(resp.text)
    assert items[-1] == {"done": True}
    # And the loop is back to IDLE - the failed turn must not wedge it.
    assert app.state.taos_agent_loop.state.value == "idle"


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


@pytest.mark.asyncio
async def test_ensure_server_rescope_failure_keeps_cached_server(tmp_path, monkeypatch):
    """When update_agent_key returns False (re-scope no-op), the server is
    created but NOT marked as born degraded — the proxy is already running
    so this is a routing-only key-scope no-op, not a degradation. A second
    ensure call must reuse the cached server, proving no restart churn."""
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
    # Re-scope returns False — routing-only no-op, not a proxy degradation.
    mock_proxy.update_agent_key = AsyncMock(return_value=False)
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

    # First call: must create the server and NOT mark it degraded.
    await rt.ensure_taos_opencode_server(state, "gpt-4o")

    assert spawned_cfgs[0].litellm_key == "sk-persisted-9"
    # Proxy IS running, re-scope no-op → NOT degraded.
    assert state.taos_opencode_born_degraded["gpt-4o"] is False
    mock_proxy.update_agent_key.assert_awaited()
    assert len(spawned_cfgs) == 1

    # Second call: must reuse the cached server, no restart churn.
    mock_proxy.update_agent_key.reset_mock()
    await rt.ensure_taos_opencode_server(state, "gpt-4o")
    assert len(spawned_cfgs) == 1


@pytest.mark.asyncio
async def test_ensure_server_serializes_concurrent_different_models(tmp_path, monkeypatch):
    """Two concurrent requests for different models must not both start a
    server on the shared TAOS_OPENCODE_PORT. Regression for the race where the
    existing-server check and ensure_running are separated by several awaits,
    so two requests could both observe `existing is None` and double-start
    (and the stop-on-model-change path can then clobber a server the other is
    still starting)."""
    import tinyagentos.taos_agent_runtime as rt

    alive = {"count": 0, "peak": 0}

    class _FakeServer:
        def __init__(self, cfg):
            self._cfg = cfg
        async def ensure_running(self, **kwargs):
            alive["count"] += 1
            alive["peak"] = max(alive["peak"], alive["count"])
            await asyncio.sleep(0)  # widen the overlap window
        async def stop(self):
            alive["count"] -= 1
        @property
        def base_url(self):
            return f"http://127.0.0.1:{self._cfg.port}"
        def is_running(self):
            return True

    monkeypatch.setattr(rt, "OpenCodeServer", _FakeServer)

    async def _mint_key(*args, **kwargs):
        # Yield here so both coroutines interleave at the key-mint await,
        # before either caches a server — without the lock both then proceed
        # to create a server on the shared port (peak alive == 2).
        await asyncio.sleep(0)
        return "sk-test"

    mock_proxy = MagicMock()
    mock_proxy.create_agent_key = _mint_key
    mock_proxy.is_running.return_value = True

    state = SimpleNamespace(data_dir=tmp_path, llm_proxy=mock_proxy)

    await asyncio.gather(
        rt.ensure_taos_opencode_server(state, "model-a"),
        rt.ensure_taos_opencode_server(state, "model-b"),
    )

    assert alive["peak"] == 1, (
        f"two servers started concurrently (peak alive={alive['peak']}); "
        "the lifecycle must be serialized by the app-state lock"
    )


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

    assert state.taos_opencode_born_degraded["gpt-4o"] is True


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
    assert state.taos_opencode_born_degraded["gpt-4o"] is True
    assert len(spawned_cfgs) == 1

    # Proxy comes up.
    mock_proxy.is_running.return_value = True

    # Second call: proxy is ready now, so should tear down and rebuild.
    await rt.ensure_taos_opencode_server(state, "gpt-4o")

    assert len(stop_calls) == 1, "old server must have been stopped"
    assert len(spawned_cfgs) == 2, "a new server must have been created"
    assert state.taos_opencode_born_degraded["gpt-4o"] is False


@pytest.mark.asyncio
async def test_ensure_server_model_switch_clears_legacy_session_id(tmp_path, monkeypatch):
    """When the model changes, the legacy ``taos_opencode_session_id`` attr is
    set to None so the desktop chat path does not feed a stale session from the
    previous model into the new server."""
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
            stop_calls.append(f"stopped:{self._cfg.home}")

        @property
        def base_url(self):
            return f"http://127.0.0.1:{self._cfg.port}"

        def is_running(self):
            return True

    monkeypatch.setattr(rt, "OpenCodeServer", _FakeServer)

    mock_proxy = MagicMock()
    mock_proxy.is_running.return_value = True
    mock_proxy.create_agent_key = AsyncMock(return_value="sk-key-1")

    state = SimpleNamespace(
        data_dir=tmp_path,
        llm_proxy=mock_proxy,
        taos_opencode_password=None,
        taos_opencode_server=None,
        taos_opencode_model=None,
        taos_opencode_session_id=None,
    )

    # First call: create model A's server and simulate a session.
    await rt.ensure_taos_opencode_server(state, "gpt-4o")
    assert len(spawned_cfgs) == 1
    # Simulate the desktop chat path having stored a session id.
    state.taos_opencode_session_id = "ses-old-model"
    state.taos_opencode_sessions["gpt-4o"] = "ses-old-model"

    # Second call: switch to model B — must stop model A and clear the legacy
    # session id so the desktop chat path sees None for the new model.
    await rt.ensure_taos_opencode_server(state, "claude-sonnet")

    assert len(stop_calls) >= 1, "old-model server must have been stopped"
    assert len(spawned_cfgs) == 2, "a new server must have been created"
    assert state.taos_opencode_session_id is None, (
        "legacy session id must be cleared after model switch; got "
        f"{state.taos_opencode_session_id!r}"
    )
    assert "gpt-4o" not in state.taos_opencode_servers
    assert "gpt-4o" not in state.taos_opencode_sessions
    assert "claude-sonnet" in state.taos_opencode_servers


@pytest.mark.asyncio
async def test_ensure_server_model_switch_preserves_home_directory(tmp_path, monkeypatch):
    """Switching models stops the old server but must NOT delete the old
    model's home directory. The per-model home IS the conversation store, so
    deleting it on model switch discards history and re-pays the one-time SQLite
    migration the 180s ensure_running deadline exists to absorb. Regression for
    the stop-on-model-change path that previously ``shutil.rmtree``'d the
    previous model's home."""
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
    mock_proxy.is_running.return_value = True
    mock_proxy.create_agent_key = AsyncMock(return_value="sk-key-1")

    state = SimpleNamespace(
        data_dir=tmp_path,
        llm_proxy=mock_proxy,
        taos_opencode_password=None,
        taos_opencode_server=None,
        taos_opencode_model=None,
        taos_opencode_session_id=None,
    )

    # Start model A, then drop a marker file into its home directory.
    await rt.ensure_taos_opencode_server(state, "gpt-4o")
    home_a = Path(spawned_cfgs[0].home)
    home_a.mkdir(parents=True, exist_ok=True)
    marker = home_a / "conversation-history.sqlite"
    marker.write_text("precious conversation history")

    # Switch to model B (stops A), then switch back to A.
    await rt.ensure_taos_opencode_server(state, "claude-sonnet")
    await rt.ensure_taos_opencode_server(state, "gpt-4o")

    assert marker.exists(), (
        "model A's home directory was deleted on model switch; the per-model "
        "home is the conversation store and must survive a stop-on-model-change"
    )


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


# ---------------------------------------------------------------------------
# AgentLoop serialization: concurrent POSTs no longer race on the shared
# opencode session (tsk-icpt4i)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_concurrent_second_request_queued_and_surfaced(client, app, monkeypatch):
    """While a turn is in flight, a second POST gets a single queued-notice
    frame + done, and after the first turn completes its message is surfaced
    in the first stream's tail before the final done."""
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

    started = asyncio.Event()
    release = asyncio.Event()

    class _BlockingAdapter:
        def __init__(self, cfg, sink):
            self._sink = sink
            self.session_id = None

        async def ensure_session(self):
            self.session_id = "ses_block"

        async def prompt(self, text, trace_id=None, attachments=None):
            self._sink({"kind": "delta", "content": f"reply:{text}"})
            started.set()
            await release.wait()
            self._sink({"kind": "final", "content": f"reply:{text}"})

        async def close(self):
            pass

    monkeypatch.setattr(
        "tinyagentos.routes.taos_agent.OpenCodeAdapter",
        _BlockingAdapter,
    )

    first = asyncio.create_task(client.post(
        "/api/taos-agent/chat",
        json={"messages": [{"role": "user", "content": "first message"}]},
    ))
    await asyncio.wait_for(started.wait(), timeout=5)

    # Second request while the first turn is mid-flight → queued notice.
    resp2 = await client.post(
        "/api/taos-agent/chat",
        json={"messages": [{"role": "user", "content": "second message"}]},
    )
    assert resp2.status_code == 200
    items2 = _parse_ndjson(resp2.text)
    assert len(items2) == 2
    assert "queued" in items2[0]["delta"].lower()
    assert items2[-1] == {"done": True}

    # Let the first turn complete: its stream tail surfaces the queued message.
    release.set()
    resp1 = await asyncio.wait_for(first, timeout=5)
    assert resp1.status_code == 200
    items1 = _parse_ndjson(resp1.text)
    tail = [
        i for i in items1
        if "queued message received while working" in i.get("delta", "")
    ]
    assert len(tail) == 1
    assert "second message" in tail[0]["delta"]
    assert items1[-1] == {"done": True}
    # The queued frame comes after the turn's own reply delta.
    assert items1.index(tail[0]) > items1.index({"delta": "reply:first message"})


@pytest.mark.asyncio
async def test_chat_loop_idle_again_after_turn(client, app, monkeypatch):
    """After a completed turn the loop is IDLE, so the next POST is driven
    immediately (no stale queued notice)."""
    from tinyagentos.agent_loop import LoopState

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
            self.session_id = "ses_seq"

        async def prompt(self, text, trace_id=None, attachments=None):
            self._sink({"kind": "delta", "content": "pong"})
            self._sink({"kind": "final", "content": "pong"})

        async def close(self):
            pass

    monkeypatch.setattr(
        "tinyagentos.routes.taos_agent.OpenCodeAdapter",
        _NormalAdapter,
    )

    for _ in range(2):
        resp = await client.post(
            "/api/taos-agent/chat",
            json={"messages": [{"role": "user", "content": "Hi"}]},
        )
        assert resp.status_code == 200
        items = _parse_ndjson(resp.text)
        assert {"delta": "pong"} in items
        assert not any("queued" in i.get("delta", "").lower() for i in items)
    assert app.state.taos_agent_loop.state is LoopState.IDLE


# ---------------------------------------------------------------------------
# GET /api/taos-agent/status — scoped payload (tsk-icpt4i)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_defaults_to_idle_without_loop(client):
    resp = await client.get("/api/taos-agent/status")
    assert resp.status_code == 200
    assert resp.json() == {
        "state": "idle",
        "current_turn_id": None,
        "queued_count": 0,
        "subagents": [],
    }


@pytest.mark.asyncio
async def test_status_scopes_subagent_fields(client, app):
    """result/error stay server-side: subagent dicts expose ONLY
    id/task/state/started_at."""
    from tinyagentos.agent_loop import AgentLoop

    loop = AgentLoop()
    app.state.taos_agent_loop = loop

    async def ok_worker(progress):
        return {"secret": "server-side result payload"}

    async def bad_worker(progress):
        raise RuntimeError("server-side error detail")

    ok_id = await loop.spawn_subagent("index files", ok_worker)
    bad_id = await loop.spawn_subagent("doomed job", bad_worker)
    await loop.await_subagent(ok_id)
    with pytest.raises(RuntimeError, match="server-side error detail"):
        await loop.await_subagent(bad_id)

    resp = await client.get("/api/taos-agent/status")
    assert resp.status_code == 200
    data = resp.json()
    assert set(data.keys()) == {"state", "current_turn_id", "queued_count", "subagents"}
    assert data["state"] == "idle"
    assert data["queued_count"] == 0
    assert len(data["subagents"]) == 2
    by_id = {s["id"]: s for s in data["subagents"]}
    assert by_id[ok_id]["state"] == "completed"
    assert by_id[bad_id]["state"] == "failed"
    for sub in data["subagents"]:
        assert set(sub.keys()) == {"id", "task", "state", "started_at"}
        assert "result" not in sub
        assert "error" not in sub
    # The payloads must not leak anywhere in the response body.
    assert "server-side" not in resp.text
