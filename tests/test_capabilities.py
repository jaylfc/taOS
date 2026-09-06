"""Tests for the dynamic capability system."""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.capabilities import CapabilityChecker, CAPABILITIES, UNLOCK_HINTS
from tinyagentos.hardware import HardwareProfile, CpuInfo, GpuInfo, NpuInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_profile(
    ram_mb=8192,
    arch="aarch64",
    gpu_type="none",
    gpu_vram_mb=0,
    cuda=False,
    rocm=False,
    npu_type="none",
) -> HardwareProfile:
    return HardwareProfile(
        cpu=CpuInfo(arch=arch, model="test", cores=4),
        ram_mb=ram_mb,
        gpu=GpuInfo(type=gpu_type, vram_mb=gpu_vram_mb, cuda=cuda, rocm=rocm),
        npu=NpuInfo(type=npu_type),
    )


class FakeWorker:
    def __init__(self, hardware, status="online"):
        self.hardware = hardware
        self.status = status


class FakeCluster:
    def __init__(self, workers=None):
        self._workers = workers or []

    def get_workers(self):
        return self._workers


# ---------------------------------------------------------------------------
# Basic capability checks — local hardware only
# ---------------------------------------------------------------------------

class TestCapabilityCheckerLocal:
    def test_keyword_search_always_available(self):
        hw = _make_profile(ram_mb=512)
        checker = CapabilityChecker(hw)
        assert checker.is_available("keyword-search") is True

    def test_unknown_capability_returns_false(self):
        hw = _make_profile()
        checker = CapabilityChecker(hw)
        assert checker.is_available("nonexistent-cap") is False

    def test_ram_based_caps_available(self):
        hw = _make_profile(ram_mb=8192)
        checker = CapabilityChecker(hw)
        assert checker.is_available("agent-deploy") is True
        assert checker.is_available("chat-small") is True
        assert checker.is_available("embedding") is True
        assert checker.is_available("semantic-search") is True
        assert checker.is_available("tts") is True
        assert checker.is_available("stt") is True

    def test_ram_based_caps_insufficient(self):
        hw = _make_profile(ram_mb=1024)
        checker = CapabilityChecker(hw)
        assert checker.is_available("agent-deploy") is False
        assert checker.is_available("chat-small") is False

    def test_vram_based_caps_no_gpu(self):
        hw = _make_profile(ram_mb=8192)
        checker = CapabilityChecker(hw)
        assert checker.is_available("chat-large") is False
        assert checker.is_available("lora-training") is False
        assert checker.is_available("full-training") is False

    def test_vram_based_caps_with_nvidia(self):
        hw = _make_profile(gpu_type="nvidia", gpu_vram_mb=12288, cuda=True)
        checker = CapabilityChecker(hw)
        assert checker.is_available("chat-large") is True
        assert checker.is_available("image-generation-gpu") is True
        assert checker.is_available("video-generation") is True
        assert checker.is_available("lora-training") is True

    def test_vram_based_caps_with_amd(self):
        hw = _make_profile(gpu_type="amd", gpu_vram_mb=8192, rocm=True)
        checker = CapabilityChecker(hw)
        assert checker.is_available("chat-large") is True
        assert checker.is_available("image-generation-gpu") is True

    def test_nvidia_without_cuda_ignored(self):
        hw = _make_profile(gpu_type="nvidia", gpu_vram_mb=12288, cuda=False)
        checker = CapabilityChecker(hw)
        assert checker.is_available("chat-large") is False

    def test_npu_image_generation(self):
        hw = _make_profile(npu_type="rknpu")
        checker = CapabilityChecker(hw)
        assert checker.is_available("image-generation-npu") is True

    def test_npu_image_generation_wrong_type(self):
        hw = _make_profile(npu_type="hailo")
        checker = CapabilityChecker(hw)
        assert checker.is_available("image-generation-npu") is False

    def test_reranking_with_npu_fallback(self):
        hw = _make_profile(npu_type="rknpu")
        checker = CapabilityChecker(hw)
        # No VRAM, but or_npu + NPU present => available
        assert checker.is_available("reranking") is True

    def test_reranking_no_vram_no_npu(self):
        hw = _make_profile()
        checker = CapabilityChecker(hw)
        assert checker.is_available("reranking") is False

    def test_rknn_conversion_needs_x86(self):
        hw = _make_profile(arch="aarch64")
        checker = CapabilityChecker(hw)
        assert checker.is_available("rknn-conversion") is False

    def test_rknn_conversion_x86(self):
        hw = _make_profile(arch="x86_64")
        checker = CapabilityChecker(hw)
        assert checker.is_available("rknn-conversion") is True

    def test_full_training_needs_huge_vram(self):
        hw = _make_profile(gpu_type="nvidia", gpu_vram_mb=16384, cuda=True)
        checker = CapabilityChecker(hw)
        assert checker.is_available("full-training") is False

        hw2 = _make_profile(gpu_type="nvidia", gpu_vram_mb=24576, cuda=True)
        checker2 = CapabilityChecker(hw2)
        assert checker2.is_available("full-training") is True


# ---------------------------------------------------------------------------
# Cluster aggregation
# ---------------------------------------------------------------------------

class TestCapabilityCheckerCluster:
    def test_cluster_adds_vram(self):
        hw = _make_profile(ram_mb=4096)
        workers = [
            FakeWorker(hardware={
                "ram_mb": 32768,
                "gpu": {"type": "nvidia", "vram_mb": 24576, "cuda": True},
                "npu": {"type": "none"},
                "cpu": {"arch": "x86_64"},
            }),
        ]
        cluster = FakeCluster(workers)
        checker = CapabilityChecker(hw, cluster)
        assert checker.is_available("chat-large") is True
        assert checker.is_available("full-training") is True

    def test_cluster_adds_architecture(self):
        hw = _make_profile(arch="aarch64")
        workers = [
            FakeWorker(hardware={
                "ram_mb": 16384,
                "gpu": {},
                "npu": {"type": "none"},
                "cpu": {"arch": "x86_64"},
            }),
        ]
        cluster = FakeCluster(workers)
        checker = CapabilityChecker(hw, cluster)
        assert checker.is_available("rknn-conversion") is True

    def test_cluster_adds_npu(self):
        hw = _make_profile(arch="x86_64")
        workers = [
            FakeWorker(hardware={
                "ram_mb": 8192,
                "gpu": {},
                "npu": {"type": "rknpu"},
                "cpu": {"arch": "aarch64"},
            }),
        ]
        cluster = FakeCluster(workers)
        checker = CapabilityChecker(hw, cluster)
        assert checker.is_available("image-generation-npu") is True

    def test_offline_workers_ignored(self):
        hw = _make_profile(ram_mb=4096)
        workers = [
            FakeWorker(
                hardware={
                    "ram_mb": 32768,
                    "gpu": {"type": "nvidia", "vram_mb": 24576, "cuda": True},
                    "npu": {"type": "none"},
                    "cpu": {"arch": "x86_64"},
                },
                status="offline",
            ),
        ]
        cluster = FakeCluster(workers)
        checker = CapabilityChecker(hw, cluster)
        assert checker.is_available("chat-large") is False

    def test_cluster_ram_uses_max(self):
        hw = _make_profile(ram_mb=2048)
        workers = [
            FakeWorker(hardware={
                "ram_mb": 16384,
                "gpu": {},
                "npu": {"type": "none"},
                "cpu": {"arch": "aarch64"},
            }),
        ]
        cluster = FakeCluster(workers)
        checker = CapabilityChecker(hw, cluster)
        assert checker.is_available("chat-small") is True
        assert checker.is_available("music-generation") is True


# ---------------------------------------------------------------------------
# Unlock hints
# ---------------------------------------------------------------------------

class TestUnlockHints:
    def test_hint_for_locked_capability(self):
        hw = _make_profile(ram_mb=4096)
        checker = CapabilityChecker(hw)
        hint = checker.get_unlock_hint("chat-large")
        assert hint is not None
        assert "GPU" in hint or "VRAM" in hint

    def test_no_hint_for_available_capability(self):
        hw = _make_profile(ram_mb=8192)
        checker = CapabilityChecker(hw)
        assert checker.get_unlock_hint("keyword-search") is None

    def test_no_hint_for_cap_without_defined_hint(self):
        hw = _make_profile(ram_mb=1024)
        checker = CapabilityChecker(hw)
        # agent-deploy is locked but has no UNLOCK_HINTS entry
        assert checker.get_unlock_hint("agent-deploy") is None

    def test_all_hints_reference_existing_capabilities(self):
        for cap_name in UNLOCK_HINTS:
            assert cap_name in CAPABILITIES


# ---------------------------------------------------------------------------
# get_all_capabilities
# ---------------------------------------------------------------------------

class TestGetAllCapabilities:
    def test_returns_all_capabilities(self):
        hw = _make_profile(ram_mb=8192)
        checker = CapabilityChecker(hw)
        result = checker.get_all_capabilities()
        assert set(result.keys()) == set(CAPABILITIES.keys())
        for cap_name, info in result.items():
            assert "available" in info
            assert "hint" in info

    def test_available_caps_have_no_hint(self):
        hw = _make_profile(ram_mb=8192)
        checker = CapabilityChecker(hw)
        result = checker.get_all_capabilities()
        for cap_name, info in result.items():
            if info["available"]:
                assert info["hint"] is None


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_capabilities(client):
    resp = await client.get("/api/capabilities")
    assert resp.status_code == 200
    data = resp.json()
    assert "capabilities" in data
    caps = data["capabilities"]
    # keyword-search should always be available
    assert caps["keyword-search"]["available"] is True
    assert caps["keyword-search"]["hint"] is None
    # All capability names present
    for cap_name in CAPABILITIES:
        assert cap_name in caps
        assert "available" in caps[cap_name]
        assert "hint" in caps[cap_name]
