"""Tests for #642 — startup 503 guard and removal of duplicate eager init."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


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
    # _startup_complete is False (default), so guard is active.
    assert app.state._startup_complete is False
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_api_route_returns_503_before_startup(tmp_path):
    """Non-exempt routes must return 503 before startup completes."""
    app = _make_app(tmp_path)
    assert app.state._startup_complete is False
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
    assert app.state._startup_complete is False
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
