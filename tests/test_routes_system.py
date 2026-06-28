"""Endpoint tests for tinyagentos/routes/system.py."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.restart_orchestrator import RestartOrchestrator


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
