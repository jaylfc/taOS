"""Endpoint tests for tinyagentos/routes/system.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.restart_orchestrator import RestartOrchestrator
from tinyagentos.routes import system as system_routes


# ---------------------------------------------------------------------------
# Minimal hardware stubs mirroring the real dataclasses
# ---------------------------------------------------------------------------

@dataclass
class _CpuInfo:
    arch: str = "aarch64"
    model: str = "Cortex-A76"
    cores: int = 4
    soc: str = "RK3588"


@dataclass
class _GpuInfo:
    type: str = "mali"
    model: str = "Valhall"
    vram_mb: int = 0
    vulkan: bool = False
    cuda: bool = False
    rocm: bool = False


@dataclass
class _NpuInfo:
    type: str = "rknpu"
    device: str = ""
    tops: int = 6
    cores: int = 3


@dataclass
class _DiskInfo:
    total_gb: int = 64
    free_gb: int = 32
    type: str = "emmc"


@dataclass
class _OsInfo:
    distro: str = "Ubuntu"
    version: str = "24.04"
    kernel: str = "6.1.0"


@dataclass
class _HardwareProfile:
    cpu: _CpuInfo = field(default_factory=_CpuInfo)
    ram_mb: int = 8192
    npu: _NpuInfo = field(default_factory=_NpuInfo)
    gpu: _GpuInfo = field(default_factory=_GpuInfo)
    disk: _DiskInfo = field(default_factory=_DiskInfo)
    os: _OsInfo = field(default_factory=_OsInfo)
    wsl: bool = False
    mem_note: str = ""

    @property
    def profile_id(self) -> str:
        return "arm-npu-8gb"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPrepareShutdown:
    @pytest.mark.asyncio
    async def test_prepare_shutdown_returns_200(self, client, monkeypatch):
        fake_orchestrator = RestartOrchestrator(client._transport.app.state)
        monkeypatch.setattr(
            client._transport.app.state, "orchestrator", fake_orchestrator
        )
        resp = await client.post("/api/system/prepare-shutdown")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_prepare_shutdown_response_shape(self, client, monkeypatch):
        fake_orchestrator = RestartOrchestrator(client._transport.app.state)
        monkeypatch.setattr(
            client._transport.app.state, "orchestrator", fake_orchestrator
        )
        data = (await client.post("/api/system/prepare-shutdown")).json()
        assert "status" in data
        assert data["status"] == "ready"
        assert "report" in data

    @pytest.mark.asyncio
    async def test_prepare_shutdown_no_orchestrator_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(
            client._transport.app.state, "orchestrator", None
        )
        resp = await client.post("/api/system/prepare-shutdown")
        assert resp.status_code == 503
        data = resp.json()
        assert "error" in data


class TestRestartPrepare:
    @pytest.mark.asyncio
    async def test_restart_prepare_returns_200(self, client, monkeypatch):
        fake_orchestrator = RestartOrchestrator(client._transport.app.state)
        monkeypatch.setattr(
            client._transport.app.state, "orchestrator", fake_orchestrator
        )
        monkeypatch.setattr(
            client._transport.app.state, "auto_updater", None, raising=False
        )
        with patch("tinyagentos.routes.system._do_restart"):
            resp = await client.post("/api/system/restart/prepare")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_restart_prepare_response_shape(self, client, monkeypatch):
        fake_orchestrator = RestartOrchestrator(client._transport.app.state)
        monkeypatch.setattr(
            client._transport.app.state, "orchestrator", fake_orchestrator
        )
        monkeypatch.setattr(
            client._transport.app.state, "auto_updater", None, raising=False
        )
        with patch("tinyagentos.routes.system._do_restart"):
            data = (await client.post("/api/system/restart/prepare")).json()
        assert "status" in data
        assert data["status"] == "restarting"

    @pytest.mark.asyncio
    async def test_restart_prepare_with_auto_updater(self, client, monkeypatch):
        fake_orchestrator = RestartOrchestrator(client._transport.app.state)
        fake_updater = MagicMock()
        fake_updater._current_commit = AsyncMock(return_value="abc123")
        monkeypatch.setattr(
            client._transport.app.state, "orchestrator", fake_orchestrator
        )
        monkeypatch.setattr(
            client._transport.app.state, "auto_updater", fake_updater, raising=False
        )
        with (
            patch("tinyagentos.routes.system._do_restart"),
            patch("tinyagentos.routes.system.write_pending_restart"),
        ):
            resp = await client.post("/api/system/restart/prepare")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_restart_prepare_no_orchestrator_still_returns_200(
        self, client, monkeypatch
    ):
        monkeypatch.setattr(
            client._transport.app.state, "orchestrator", None
        )
        monkeypatch.setattr(
            client._transport.app.state, "auto_updater", None, raising=False
        )
        with patch("tinyagentos.routes.system._do_restart"):
            resp = await client.post("/api/system/restart/prepare")
        assert resp.status_code == 200


class TestHardwareRefresh:
    @pytest.mark.asyncio
    async def test_hardware_refresh_returns_200(self, client):
        profile = _HardwareProfile()
        with patch(
            "tinyagentos.hardware.get_hardware_profile",
            return_value=profile,
        ):
            resp = await client.post("/api/system/hardware/refresh")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_hardware_refresh_response_shape(self, client):
        profile = _HardwareProfile()
        with patch(
            "tinyagentos.hardware.get_hardware_profile",
            return_value=profile,
        ):
            data = (await client.post("/api/system/hardware/refresh")).json()
        assert "profile_id" in data
        assert "cpu" in data
        assert "ram_mb" in data
        assert "npu" in data
        assert "gpu" in data
        assert "disk" in data
        assert "os" in data

    @pytest.mark.asyncio
    async def test_hardware_refresh_no_data_dir_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(
            client._transport.app.state, "data_dir", None
        )
        resp = await client.post("/api/system/hardware/refresh")
        assert resp.status_code == 503
        data = resp.json()
        assert "error" in data


class TestRestartStatus:
    @pytest.mark.asyncio
    async def test_restart_status_returns_idle_state(self, client, monkeypatch):
        fake_orchestrator = RestartOrchestrator(client._transport.app.state)
        monkeypatch.setattr(
            client._transport.app.state, "orchestrator", fake_orchestrator
        )
        resp = await client.get("/api/system/restart/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "phase" in data
        assert "reason" in data
        assert "started_at" in data
        assert "agents" in data
        assert data["phase"] == "idle"
        assert data["reason"] == ""
        assert data["started_at"] == 0
        assert data["agents"] == {}

    @pytest.mark.asyncio
    async def test_restart_status_no_orchestrator_returns_503(self, client, monkeypatch):
        monkeypatch.setattr(
            client._transport.app.state, "orchestrator", None
        )
        resp = await client.get("/api/system/restart/status")
        assert resp.status_code == 503
        data = resp.json()
        assert "error" in data


async def _member_client(app) -> AsyncClient:
    """Cookie'd client for a non-admin member session (mirrors settings authz)."""
    auth_mgr = app.state.auth
    invite_code = auth_mgr.add_user_invite("member", "admin")
    auth_mgr.complete_invite("member", invite_code, "Test Member", "", "memberpass123456")
    member = auth_mgr.find_user("member")
    token = auth_mgr.create_session(user_id=member["id"], long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )


class TestRestartAiStack:
    """POST /api/system/ai-stack/restart -- issue #1743 backend recovery.

    The endpoint derives its target set via ``_managed_ai_units`` (tested
    separately below) and restarts each via ``backend_services.service_action``;
    these tests stub both to exercise the aggregation/status logic.
    """

    @pytest.mark.asyncio
    async def test_all_ok(self, client, monkeypatch):
        monkeypatch.setattr(
            system_routes, "_managed_ai_units",
            AsyncMock(return_value=[("rkllama.service", "system"), ("qmd.service", "system")]),
        )
        monkeypatch.setattr(
            system_routes.backend_services, "service_action",
            AsyncMock(side_effect=[
                {"unit": "rkllama.service", "ok": True, "scope": "system"},
                {"unit": "qmd.service", "ok": True, "scope": "system"},
            ]),
        )
        resp = await client.post("/api/system/ai-stack/restart")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["restarted"] == ["rkllama.service", "qmd.service"]
        assert data["failed"] == []

    @pytest.mark.asyncio
    async def test_partial_recovery_reports_partial(self, client, monkeypatch):
        monkeypatch.setattr(
            system_routes, "_managed_ai_units",
            AsyncMock(return_value=[("rkllama.service", "system"), ("qmd.service", "system")]),
        )
        monkeypatch.setattr(
            system_routes.backend_services, "service_action",
            AsyncMock(side_effect=[
                {"unit": "rkllama.service", "ok": True, "scope": "system"},
                {"unit": "qmd.service", "ok": False, "detail": "not installed"},
            ]),
        )
        data = (await client.post("/api/system/ai-stack/restart")).json()
        # A mixed result is "partial" (not "ok"), so the UI can flag it.
        assert data["status"] == "partial"
        assert data["restarted"] == ["rkllama.service"]
        assert len(data["failed"]) == 1
        assert data["failed"][0]["unit"] == "qmd.service"

    @pytest.mark.asyncio
    async def test_all_fail_reports_failed(self, client, monkeypatch):
        monkeypatch.setattr(
            system_routes, "_managed_ai_units",
            AsyncMock(return_value=[("rkllama.service", "system"), ("qmd.service", "system")]),
        )
        monkeypatch.setattr(
            system_routes.backend_services, "service_action",
            AsyncMock(side_effect=[
                {"unit": "rkllama.service", "ok": False, "detail": "auth required"},
                {"unit": "qmd.service", "ok": False, "detail": "auth required"},
            ]),
        )
        data = (await client.post("/api/system/ai-stack/restart")).json()
        assert data["status"] == "failed"
        assert data["restarted"] == []
        assert len(data["failed"]) == 2

    @pytest.mark.asyncio
    async def test_noop_when_no_managed_backends_installed(self, client, monkeypatch):
        # No managed backend installed on this node (e.g. non-systemd dev host):
        # report noop rather than a misleading "failed", and never call restart.
        monkeypatch.setattr(
            system_routes, "_managed_ai_units", AsyncMock(return_value=[]),
        )
        action = AsyncMock()
        monkeypatch.setattr(system_routes.backend_services, "service_action", action)
        data = (await client.post("/api/system/ai-stack/restart")).json()
        assert data["status"] == "noop"
        assert data["restarted"] == [] and data["failed"] == []
        action.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_admin_rejected_403(self, client, app, monkeypatch):
        # Guard should reject before any restart is attempted.
        units = AsyncMock(return_value=[("qmd.service", "system")])
        monkeypatch.setattr(system_routes, "_managed_ai_units", units)
        member_client = await _member_client(app)
        try:
            resp = await member_client.post("/api/system/ai-stack/restart")
        finally:
            await member_client.aclose()
        assert resp.status_code == 403
        units.assert_not_called()


class TestManagedAiUnits:
    """_managed_ai_units: derive core + manifest units, filter to installed."""

    def _request(self, tmp_path):
        req = MagicMock()
        req.app.state.registry.catalog_dir = tmp_path
        return req

    @pytest.mark.asyncio
    async def test_unions_core_and_manifest_when_installed(self, tmp_path, monkeypatch):
        from tinyagentos.cluster.backend_services import ManagedBackend

        rk = ManagedBackend(
            id="rkllama", unit="rkllama.service", scope="system",
            health_url="http://localhost:7833/api/tags", health_expect='"models"',
        )
        monkeypatch.setattr(
            system_routes.backend_services, "load_managed_backends", lambda root: [rk]
        )
        monkeypatch.setattr(
            system_routes.backend_services, "unit_state",
            AsyncMock(return_value={"installed": True}),
        )
        units = await system_routes._managed_ai_units(self._request(tmp_path))
        # qmd (core, no manifest) + rkllama (from manifest), both installed.
        assert set(u for u, _ in units) == {"qmd.service", "rkllama.service"}

    @pytest.mark.asyncio
    async def test_skips_units_not_installed_on_node(self, tmp_path, monkeypatch):
        from tinyagentos.cluster.backend_services import ManagedBackend

        rk = ManagedBackend(
            id="rkllama", unit="rkllama.service", scope="system",
            health_url="", health_expect="",
        )
        monkeypatch.setattr(
            system_routes.backend_services, "load_managed_backends", lambda root: [rk]
        )

        async def fake_state(unit, prefer=None):
            # rkllama has migrated to another node; only qmd is installed here.
            return {"installed": unit == "qmd.service"}

        monkeypatch.setattr(system_routes.backend_services, "unit_state", fake_state)
        units = await system_routes._managed_ai_units(self._request(tmp_path))
        assert [u for u, _ in units] == ["qmd.service"]

    @pytest.mark.asyncio
    async def test_catalog_load_failure_still_yields_core(self, tmp_path, monkeypatch):
        # A broken catalog must not drop qmd from recovery.
        def boom(root):
            raise RuntimeError("catalog unreadable")

        monkeypatch.setattr(
            system_routes.backend_services, "load_managed_backends", boom
        )
        monkeypatch.setattr(
            system_routes.backend_services, "unit_state",
            AsyncMock(return_value={"installed": True}),
        )
        units = await system_routes._managed_ai_units(self._request(tmp_path))
        assert [u for u, _ in units] == ["qmd.service"]
