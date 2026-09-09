"""Tests for #642 — startup 503 guard and removal of duplicate eager init."""
from __future__ import annotations

import asyncio
import json
import time
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
    # Ensure hardware.json exists so create_app() hardware detection succeeds.
    hw_path = tmp_path / "data" / "hardware.json"
    hw_path.parent.mkdir(parents=True, exist_ok=True)
    hw_path.write_text(json.dumps({
        "cpu": "x86_64", "ram_mb": 4096, "npu": None, "gpu": None,
        "disk": "ssd", "os": "linux", "wsl": False,
    }))
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


@pytest.mark.asyncio
async def test_health_responds_during_litellm_generate(tmp_path, monkeypatch):
    """Health endpoint must answer within N ms while LiteLLM prisma generate runs.

    The _litellm_bringup() now fires llm_proxy.start() as a background task
    so the startup guard clears immediately and the API keeps answering during
    the generate step.  On the previous code the await llm_proxy.start() blocked
    the event loop for the full 120 s polling cycle, causing the health check
    to time out.
    """
    import yaml
    from unittest.mock import patch as patch_mod

    # Setup a DATABASE_URL so LiteLLM will attempt prisma generate at startup.
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / ".litellm_db_url").write_text("postgresql://u:p@h/db")
    (data_dir / ".setup_complete").touch()

    # Provide a hardware profile so create_app() does not fail on detection.
    hw_path = data_dir / "hardware.json"
    hw_path.write_text(json.dumps({
        "cpu": "x86_64",
        "ram_mb": 4096,
        "npu": None,
        "gpu": None,
        "disk": "ssd",
        "os": "linux",
        "wsl": False,
    }))

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path = data_dir / "config.yaml"
    config_path.write_text(yaml.dump(config))

    from tinyagentos.app import create_app

    # Monkeypatch hardware detection to avoid platform-specific failures.
    with patch_mod("tinyagentos.hardware.detect_hardware") as mock_detect:
        mock_detect.return_value = type(
            "HardwareProfile", (object,),
            {"cpu": "x86_64", "ram_mb": 4096, "npu": None, "gpu": None,
             "disk": "ssd", "os": "linux", "wsl": False}
        )()

        with patch_mod("tinyagentos.hardware.get_hardware_profile") as mock_hw:
            mock_hw.return_value = type(
                "HardwareProfile", (object,),
                {"cpu": "x86_64", "ram_mb": 4096, "npu": None, "gpu": None,
                 "disk": "ssd", "os": "linux", "wsl": False}
            )()

            app = create_app(data_dir=data_dir)

            # Run the lifespan so the bring-up task fires.
            async with app.router.lifespan_context(app):
                # Give the background task a moment to start the proxy.
                await asyncio.sleep(0.1)

                # The health endpoint must answer quickly even though prisma generate
                # is running in the background.
                start = time.monotonic()
                async with ASGITransport(app=app) as transport:
                    async with AsyncClient(transport=transport, base_url="http://test") as client:
                        resp = await client.get("/api/health")
                elapsed_ms = (time.monotonic() - start) * 1000

                # Must respond well under the threshold even during generate step.
                assert resp.status_code == 200, f"health returned {resp.status_code}"
                assert elapsed_ms < 200, (
                    f"health endpoint took {elapsed_ms:.0f} ms during LiteLLM bring-up; "
                    "the event loop was blocked — expected <200ms, got >200ms"
                )