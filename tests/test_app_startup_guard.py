"""Tests for #642 — startup 503 guard and removal of duplicate eager init."""
from __future__ import annotations

import asyncio
import threading
import time

import pytest
from httpx import ASGITransport, AsyncClient
from unittest.mock import AsyncMock, MagicMock, patch


def _make_app(tmp_path):
    import yaml
    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    from tinyagentos.app import create_app
    return create_app(data_dir=tmp_path)


# ---------------------------------------------------------------------------
# 503 guard — requests before lifespan completes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health_endpoint_exempt_before_startup(tmp_path):
    """/api/health must respond 200 even before startup completes."""
    app = _make_app(tmp_path)
    # Arm the guard as the lifespan would at startup entry.
    app.state._startup_complete = False
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_route_returns_503_before_startup(tmp_path):
    """Non-exempt routes must return 503 before startup completes."""
    app = _make_app(tmp_path)
    # Arm the guard as the lifespan would at startup entry.
    app.state._startup_complete = False
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/agents")
    assert resp.status_code == 503
    assert "starting" in resp.json().get("detail", "").lower()


@pytest.mark.asyncio
async def test_api_route_passes_after_startup_flag_set(tmp_path):
    """Setting _startup_complete = True must let requests through."""
    app = _make_app(tmp_path)
    app.state._startup_complete = True
    # Auth will block without a valid session; use the exempt /api/health.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_static_path_exempt_before_startup(tmp_path):
    """/static/* paths must not be blocked by the startup guard."""
    app = _make_app(tmp_path)
    # Arm the guard as the lifespan would at startup entry.
    app.state._startup_complete = False
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # /static/ will 404 (no files in tmp), but must not 503.
        resp = await client.get("/static/favicon.ico")
    assert resp.status_code != 503


# ---------------------------------------------------------------------------
# Double-init guard — lifespan-owned objects must be None at create_app time
# ---------------------------------------------------------------------------

def test_wants_reply_is_none_before_lifespan(tmp_path):
    """wants_reply must be None at create_app() — lifespan owns init."""
    app = _make_app(tmp_path)
    assert app.state.wants_reply is None


def test_typing_is_none_before_lifespan(tmp_path):
    """typing must be None at create_app() — lifespan owns init."""
    app = _make_app(tmp_path)
    assert app.state.typing is None


def test_mcp_supervisor_is_none_before_lifespan(tmp_path):
    """mcp_supervisor must be None at create_app() — lifespan owns init."""
    app = _make_app(tmp_path)
    assert app.state.mcp_supervisor is None


def test_orchestrator_is_none_before_lifespan(tmp_path):
    """orchestrator must be None at create_app() — lifespan owns init."""
    app = _make_app(tmp_path)
    assert app.state.orchestrator is None


def test_trace_registry_is_none_before_lifespan(tmp_path):
    """trace_registry must be None at create_app() — lifespan owns init."""
    app = _make_app(tmp_path)
    assert app.state.trace_registry is None


def test_bridge_sessions_is_none_before_lifespan(tmp_path):
    """bridge_sessions must be None at create_app() — lifespan owns init."""
    app = _make_app(tmp_path)
    assert app.state.bridge_sessions is None


# ---------------------------------------------------------------------------
# LiteLLM background bring-up: _startup_complete goes True without proxy
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_startup_complete_without_litellm(tmp_path, monkeypatch):
    """_startup_complete must go True even if LiteLLM never starts.

    The proxy bring-up now runs in a supervised background task. This
    test stubs the proxy to never become ready and asserts that the
    startup guard is lifted regardless.
    """
    import yaml
    from unittest.mock import AsyncMock, MagicMock, patch

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    # Stub llm_proxy.start to never resolve (proxy never becomes ready).
    proxy_stub = MagicMock()
    proxy_stub.is_running.return_value = False
    proxy_stub.port = 7834
    proxy_stub.start = AsyncMock(return_value=False)
    proxy_stub.stop = MagicMock()

    with patch("tinyagentos.app.LLMProxy", return_value=proxy_stub):
        from tinyagentos.app import create_app
        app = create_app(data_dir=tmp_path)
        async with app.router.lifespan_context(app):
            assert app.state._startup_complete is True


# ---------------------------------------------------------------------------
# Health endpoint responsiveness during LiteLLM bring-up
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_endpoint_responsive_during_litellm_bringup(tmp_path):
    """Health endpoint must answer within 500 ms while _pids_listening_on blocks.

    A slow lsof inside LLMProxy.start() simulates the real event-loop
    stall on the restart-over-stale-LiteLLM path. The event loop must not
    be blocked, so /api/health stays fast throughout bring-up.
    """
    import contextlib
    import yaml
    from unittest.mock import AsyncMock, MagicMock, patch
    from tinyagentos.llm_proxy import LLMProxy

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()

    slow_lsof_duration = 2.0
    lsof_started = threading.Event()
    lsof_finished = threading.Event()

    def slow_pids_listening_on(port):
        lsof_started.set()
        time.sleep(slow_lsof_duration)
        lsof_finished.set()
        return []

    mock_response = MagicMock()
    mock_response.status_code = 200

    mock_client = AsyncMock()
    mock_client.get.return_value = mock_response
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False

    proxy = LLMProxy(
        port=7834,
        controller_port=6969,
        database_url=None,
        local_token=None,
        registry=MagicMock(),
        data_dir=tmp_path,
        inhouse_keys=True,
    )

    with patch("tinyagentos.llm_proxy._pids_listening_on", side_effect=slow_pids_listening_on):
        with patch("tinyagentos.llm_proxy.httpx.AsyncClient", return_value=mock_client):
            app = _make_app(tmp_path)
            app.state._startup_complete = True
            transport = ASGITransport(app=app)

            start_task = asyncio.create_task(proxy.start([], secrets={}))
            try:
                for _ in range(200):
                    if lsof_started.is_set():
                        break
                    await asyncio.sleep(0.01)

                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    t0 = time.monotonic()
                    resp = await client.get("/api/health")
                    elapsed = time.monotonic() - t0
                assert resp.status_code == 200
                assert elapsed < 0.5, (
                    f"/api/health took {elapsed:.2f}s while lsof slept "
                    f"{slow_lsof_duration}s"
                )
                assert not lsof_finished.is_set(), (
                    "/api/health completed after _pids_listening_on finished "
                    "(event loop was blocked)"
                )
            finally:
                start_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await start_task
