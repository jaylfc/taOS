"""Route tests for disk quota endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.disk_quota import DiskQuotaMonitor


def _inject_agent(app, agent: dict):
    app.state.config.agents.append(agent)


def _set_monitor(app, monitor):
    app.state.disk_quota_monitor = monitor


def _make_monitor_mock(results=None):
    m = MagicMock(spec=DiskQuotaMonitor)
    m.scan_all = AsyncMock(return_value=results or [])
    m.resize_quota = AsyncMock()
    return m


@pytest.mark.asyncio
class TestGetAgentDisk:
    async def test_returns_disk_info_for_known_agent(self, client):
        app = client._transport.app
        agent = {
            "name": "quota-agent",
            "disk_quota_gib": 40,
            "disk_usage_gib": 5.0,
            "disk_last_checked_at": 1700000000.0,
        }
        _inject_agent(app, agent)

        resp = await client.get("/api/agents/quota-agent/disk")
        assert resp.status_code == 200
        data = resp.json()
        assert data["used_gib"] == 5.0
        assert data["quota_gib"] == 40.0
        assert data["state"] == "ok"
        assert data["percent"] == pytest.approx(0.125, abs=1e-3)
        assert data["last_checked_at"] == 1700000000.0

    async def test_404_for_unknown_agent(self, client):
        resp = await client.get("/api/agents/ghost-agent/disk")
        assert resp.status_code == 404

    async def test_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            resp = await c.get("/api/agents/test-agent/disk")
            assert resp.status_code in (401, 403)

    async def test_defaults_used_when_fields_absent(self, client):
        app = client._transport.app
        _inject_agent(app, {"name": "bare-agent"})

        resp = await client.get("/api/agents/bare-agent/disk")
        assert resp.status_code == 200
        data = resp.json()
        assert data["quota_gib"] == DiskQuotaMonitor.DEFAULT_QUOTA_GIB
        assert data["used_gib"] == 0.0
        assert data["state"] == "ok"


@pytest.mark.asyncio
class TestResizeAgentQuota:
    async def test_happy_path_btrfs(self, client):
        app = client._transport.app
        agent = {"name": "resize-agent", "disk_quota_gib": 40}
        _inject_agent(app, agent)

        monitor = _make_monitor_mock()
        monitor.resize_quota = AsyncMock(return_value={
            "ok": True,
            "agent_name": "resize-agent",
            "new_quota_gib": 60,
            "message": "quota set to 60 GiB",
        })
        _set_monitor(app, monitor)

        resp = await client.post(
            "/api/agents/resize-agent/quota",
            json={"size_gib": 60},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["new_quota_gib"] == 60

    async def test_dir_backend_returns_409(self, client):
        app = client._transport.app
        agent = {"name": "dir-agent", "disk_quota_gib": 40}
        _inject_agent(app, agent)

        monitor = _make_monitor_mock()
        monitor.resize_quota = AsyncMock(
            side_effect=ValueError("dir-backend:cannot live-resize quotas on a dir storage pool")
        )
        _set_monitor(app, monitor)

        resp = await client.post(
            "/api/agents/dir-agent/quota",
            json={"size_gib": 50},
        )
        assert resp.status_code == 409
        assert "dir" in resp.json()["detail"].lower() or "resize" in resp.json()["detail"].lower()

    async def test_unknown_agent_returns_404(self, client):
        app = client._transport.app
        monitor = _make_monitor_mock()
        monitor.resize_quota = AsyncMock(
            side_effect=ValueError("agent not found: nobody")
        )
        _set_monitor(app, monitor)

        resp = await client.post(
            "/api/agents/nobody/quota",
            json={"size_gib": 50},
        )
        assert resp.status_code == 404

    async def test_unauthenticated_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            resp = await c.post(
                "/api/agents/test-agent/quota",
                json={"size_gib": 50},
            )
            assert resp.status_code in (401, 403)


@pytest.mark.asyncio
class TestTriggerScan:
    async def test_scan_runs_and_returns_results(self, client):
        app = client._transport.app

        scan_results = [
            {"name": "alice", "used_gib": 10.0, "quota_gib": 40.0,
             "percent": 0.25, "state": "ok", "last_checked_at": 1700000001.0},
        ]
        monitor = _make_monitor_mock(results=scan_results)
        _set_monitor(app, monitor)

        resp = await client.post("/api/disk-quota/scan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scanned"] == 1
        assert len(data["results"]) == 1
        assert data["results"][0]["name"] == "alice"

        monitor.scan_all.assert_called_once()

    async def test_scan_returns_empty_when_no_agents(self, client):
        app = client._transport.app
        monitor = _make_monitor_mock(results=[])
        _set_monitor(app, monitor)

        resp = await client.post("/api/disk-quota/scan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scanned"] == 0

    async def test_unauthenticated_scan_returns_401(self, app):
        from httpx import ASGITransport, AsyncClient
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as c:
            resp = await c.post("/api/disk-quota/scan")
            assert resp.status_code in (401, 403)

    async def test_scan_builds_monitor_if_absent(self, client):
        app = client._transport.app
        # Remove any existing monitor so the route builds a fresh one
        app.state.disk_quota_monitor = None

        with patch(
            "tinyagentos.routes.disk_quota._get_or_build_monitor",
            wraps=lambda req, cfg, notif: _make_monitor_mock(results=[]),
        ) as mock_build:
            resp = await client.post("/api/disk-quota/scan")

        assert resp.status_code == 200
