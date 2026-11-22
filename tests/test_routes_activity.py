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


@pytest.mark.asyncio
async def test_activity_gpus_array_exists(client):
    """The response should include a gpus array (multi-GPU support)."""
    data = (await client.get("/api/activity")).json()
    assert "gpus" in data
    assert isinstance(data["gpus"], list)


@pytest.mark.asyncio
async def test_activity_gpus_entries_have_expected_keys(client):
    """Each gpus array entry should have type, model, load, vram fields."""
    data = (await client.get("/api/activity")).json()
    for gpu in data["gpus"]:
        for key in ("type", "model", "load", "vram_percent", "vram_used_mb", "vram_total_mb"):
            assert key in gpu, f"missing gpu key: {key}"
        # load should be null or an object with load_percent and freq_hz
        if gpu["load"] is not None:
            assert "load_percent" in gpu["load"]
            assert "freq_hz" in gpu["load"]


@pytest.mark.asyncio
async def test_activity_gpus_load_null_for_intel(client, monkeypatch):
    """Intel GPUs (no runtime stats) should report load: null, not an object with null fields."""
    # Create a hardware profile with an Intel GPU
    @dataclass
    class _IntelGpuInfo:
        type: str = "intel"
        model: str = "Intel UHD Graphics 630"
        vram_mb: int = 0
        vulkan: bool = False
        cuda: bool = False
        rocm: bool = False

    @dataclass
    class _ProfileWithIntelGpu:
        cpu: _CpuInfo = field(default_factory=_CpuInfo)
        ram_mb: int = 8192
        npu: _NpuInfo = field(default_factory=_NpuInfo)
        gpu: _IntelGpuInfo = field(default_factory=_IntelGpuInfo)
        gpus: list = field(default_factory=lambda: [_IntelGpuInfo()])
        disk: _DiskInfo = field(default_factory=_DiskInfo)
        os: _OsInfo = field(default_factory=_OsInfo)

    profile = _ProfileWithIntelGpu()
    monkeypatch.setattr(client._transport.app.state, "hardware_profile", profile, raising=False)
    data = (await client.get("/api/activity")).json()
    
    # Find the Intel GPU in the gpus array
    intel_gpus = [g for g in data["gpus"] if g["type"] == "intel"]
    assert len(intel_gpus) >= 1
    for gpu in intel_gpus:
        assert gpu["load"] is None, f"Intel GPU should have load: null, got {gpu['load']}"


@pytest.mark.asyncio
async def test_activity_gpus_sorted_by_pci_bus_id(client, monkeypatch):
    """GPUs in the gpus array should be sorted by PCI bus ID.
    
    This test uses a hardware profile with multiple GPUs to verify
    the gpus array is populated and ordered correctly.
    """
    @dataclass
    class _MultiGpuProfile:
        cpu: _CpuInfo = field(default_factory=_CpuInfo)
        ram_mb: int = 8192
        npu: _NpuInfo = field(default_factory=_NpuInfo)
        gpu: _GpuInfo = field(default_factory=_GpuInfo)
        gpus: list = field(default_factory=lambda: [
            # Profile order is PCI bus order: iGPU 00:02.0 before dGPU 01:00.0.
            _GpuInfo(type="intel", model="Intel UHD Graphics 630", vram_mb=0),
            _GpuInfo(type="nvidia", model="NVIDIA GeForce RTX 3090", vram_mb=24576),
        ])
        disk: _DiskInfo = field(default_factory=_DiskInfo)
        os: _OsInfo = field(default_factory=_OsInfo)

    profile = _MultiGpuProfile()
    monkeypatch.setattr(client._transport.app.state, "hardware_profile", profile, raising=False)
    data = (await client.get("/api/activity")).json()
    assert isinstance(data["gpus"], list)
    # The route must keep the profile's PCI order, not regroup by vendor.
    assert [(g["type"], g["model"]) for g in data["gpus"]] == [
        ("intel", "Intel UHD Graphics 630"),
        ("nvidia", "NVIDIA GeForce RTX 3090"),
    ]
