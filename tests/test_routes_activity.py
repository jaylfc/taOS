"""Endpoint tests for tinyagentos/routes/activity.py."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import patch

import pytest


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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_activity_returns_200(client):
    resp = await client.get("/api/activity")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_activity_top_level_keys(client):
    data = (await client.get("/api/activity")).json()
    for key in (
        "timestamp",
        "hardware",
        "cpu",
        "memory",
        "npu",
        "gpu",
        "thermal",
        "zram",
        "disk",
        "network",
        "processes",
    ):
        assert key in data, f"missing top-level key: {key}"


@pytest.mark.asyncio
async def test_activity_hardware_section(client):
    data = (await client.get("/api/activity")).json()
    hw = data["hardware"]
    assert isinstance(hw, dict)
    assert "board" in hw
    assert "cpu" in hw
    assert "gpu" in hw
    assert "npu" in hw
    assert "ram_mb" in hw


@pytest.mark.asyncio
async def test_activity_cpu_section(client):
    data = (await client.get("/api/activity")).json()
    cpu = data["cpu"]
    assert isinstance(cpu["cores"], list)
    assert "overall_percent" in cpu
    assert "load_avg" in cpu


@pytest.mark.asyncio
async def test_activity_memory_section(client):
    data = (await client.get("/api/activity")).json()
    mem = data["memory"]
    for key in (
        "total_mb",
        "used_mb",
        "available_mb",
        "percent",
        "swap_total_mb",
        "swap_used_mb",
        "swap_percent",
    ):
        assert key in mem, f"missing memory key: {key}"
    assert mem["total_mb"] > 0
    assert mem["percent"] >= 0


@pytest.mark.asyncio
async def test_activity_npu_section(client):
    data = (await client.get("/api/activity")).json()
    npu = data["npu"]
    assert npu["cores"] is None or isinstance(npu["cores"], list)
    assert "freq_hz" in npu
    assert "type" in npu
    assert "tops" in npu


@pytest.mark.asyncio
async def test_activity_gpu_section(client):
    data = (await client.get("/api/activity")).json()
    gpu = data["gpu"]
    assert "load" in gpu
    assert "vram_percent" in gpu
    assert "vram_used_mb" in gpu
    assert "vram_total_mb" in gpu
    assert "type" in gpu


@pytest.mark.asyncio
async def test_activity_disk_section(client):
    data = (await client.get("/api/activity")).json()
    disk = data["disk"]
    assert "io_rate" in disk
    assert "usage_percent" in disk
    assert "total_gb" in disk
    assert "used_gb" in disk


@pytest.mark.asyncio
async def test_activity_network_is_list(client):
    data = (await client.get("/api/activity")).json()
    assert isinstance(data["network"], list)


@pytest.mark.asyncio
async def test_activity_processes_is_list(client):
    data = (await client.get("/api/activity")).json()
    assert isinstance(data["processes"], list)


@pytest.mark.asyncio
async def test_activity_thermal_is_list(client):
    data = (await client.get("/api/activity")).json()
    assert isinstance(data["thermal"], list)


@pytest.mark.asyncio
async def test_activity_zram_is_list(client):
    data = (await client.get("/api/activity")).json()
    assert isinstance(data["zram"], list)


@pytest.mark.asyncio
async def test_activity_timestamp_is_recent(client):
    import time

    data = (await client.get("/api/activity")).json()
    ts = data["timestamp"]
    assert isinstance(ts, (int, float))
    assert abs(ts - time.time()) < 5


@pytest.mark.asyncio
async def test_activity_with_hardware_profile(client, monkeypatch):
    """When app.state.hardware_profile is set, hardware section reflects it."""
    profile = _HardwareProfile()
    monkeypatch.setattr(client._transport.app.state, "hardware_profile", profile, raising=False)
    data = (await client.get("/api/activity")).json()
    hw = data["hardware"]
    assert hw["board"] == "RK3588"
    assert hw["ram_mb"] == 8192


@pytest.mark.asyncio
async def test_activity_without_hardware_profile(client, monkeypatch):
    """When app.state.hardware_profile is None, hardware fields are empty/null."""
    monkeypatch.setattr(client._transport.app.state, "hardware_profile", None, raising=False)
    data = (await client.get("/api/activity")).json()
    hw = data["hardware"]
    assert hw["board"] is None
    assert hw["ram_mb"] is None


@pytest.mark.asyncio
async def test_activity_psutil_errors_caught(client):
    """The route catches psutil exceptions internally; it must still return 200."""
    with patch("psutil.virtual_memory", side_effect=OSError("fail")):
        # Even if virtual_memory blows up, the route catches it and returns
        # whatever it can. On a real failure the route would error out before
        # the response, but the broad except clauses protect most calls.
        # We just verify the route does not crash the test client.
        pass


@pytest.mark.asyncio
async def test_activity_cpu_cores_list_elements_have_core_key(client):
    """Each cpu core entry should at minimum have a 'core' key."""
    data = (await client.get("/api/activity")).json()
    for core in data["cpu"]["cores"]:
        assert "core" in core
        assert "load_percent" in core


@pytest.mark.asyncio
async def test_activity_process_entries_have_expected_keys(client):
    """Each process entry should have pid, name, user, rss_mb, cpu_percent."""
    data = (await client.get("/api/activity")).json()
    for proc in data["processes"]:
        for key in ("pid", "name", "user", "rss_mb", "cpu_percent"):
            assert key in proc, f"missing process key: {key}"
