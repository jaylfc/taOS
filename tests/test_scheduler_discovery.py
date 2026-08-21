"""Unit tests for scheduler discovery."""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from tinyagentos.scheduler.discovery import build_scheduler
from tinyagentos.scheduler.resource import Tier
from tinyagentos.scheduler.types import ResourceSignature


def _make_backend(name, type_, url="http://test", status="ok", capabilities=None, priority=1):
    from tinyagentos.scheduler.backend_catalog import BackendEntry
    return BackendEntry(
        name=name,
        type=type_,
        url=url,
        status=status,
        capabilities=set(capabilities or []),
        models=[],
        priority=priority,
    )


def _make_catalog(backends):
    catalog = mock.Mock()
    catalog.backends.return_value = list(backends)

    def backends_with_capability(cap):
        return [b for b in backends if cap in b.capabilities]

    catalog.backends_with_capability.side_effect = backends_with_capability
    return catalog


def _hardware(gpu_type=None, npu_type=None):
    return SimpleNamespace(
        gpu=SimpleNamespace(type=gpu_type),
        npu=SimpleNamespace(type=npu_type),
    )


class TestBuildScheduler:
    def test_cpu_always_registered(self, monkeypatch):
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._physical_cores", lambda: 8
        )

        catalog = _make_catalog([])
        hw = _hardware()

        sched = build_scheduler(hw, catalog)

        resources = {r.name: r for r in sched._resources.values()}
        assert "cpu-inference" in resources
        assert resources["cpu-inference"].tier == Tier.CPU

    def test_npu_registered_with_rkllama_backend_and_rknpu_hardware(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._physical_cores", lambda: 8
        )
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._probe_librknnrt_version",
            lambda: "1.3.0",
        )

        backends = [
            _make_backend(
                "rkllama-1", "rkllama",
                capabilities={"image-generation", "embedding"},
            ),
        ]
        catalog = _make_catalog(backends)
        hw = _hardware(npu_type="rknpu")

        sched = build_scheduler(hw, catalog)

        resources = {r.name: r for r in sched._resources.values()}
        assert "npu-rk3588" in resources
        npu = resources["npu-rk3588"]
        assert npu.tier == Tier.NPU
        assert npu.concurrency == 3
        assert npu.signature == ResourceSignature(
            platform="rk3588",
            runtime="librknnrt",
            runtime_version="1.3.0",
        )

    def test_npu_not_registered_without_rkllama_backend(self, monkeypatch):
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._physical_cores", lambda: 8
        )

        backends = [
            _make_backend("ollama-1", "ollama", capabilities={"llm-chat"}),
        ]
        catalog = _make_catalog(backends)
        hw = _hardware(npu_type="rknpu")

        sched = build_scheduler(hw, catalog)

        resources = {r.name: r for r in sched._resources.values()}
        assert "npu-rk3588" not in resources

    def test_npu_not_registered_when_hardware_is_not_rknpu(self, monkeypatch):
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._physical_cores", lambda: 8
        )

        backends = [
            _make_backend(
                "rkllama-1", "rkllama",
                capabilities={"image-generation"},
            ),
        ]
        catalog = _make_catalog(backends)
        hw = _hardware(npu_type=None)

        sched = build_scheduler(hw, catalog)

        resources = {r.name: r for r in sched._resources.values()}
        assert "npu-rk3588" not in resources

    def test_gpu_registered_with_gpu_backends(self, monkeypatch):
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._physical_cores", lambda: 8
        )
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._probe_nvidia_vram",
            lambda: (8192, 16384),
        )

        backends = [
            _make_backend("vllm-1", "vllm", capabilities={"llm-chat"}),
        ]
        catalog = _make_catalog(backends)
        hw = _hardware(gpu_type="cuda")

        sched = build_scheduler(hw, catalog)

        resources = {r.name: r for r in sched._resources.values()}
        assert "gpu-cuda-0" in resources
        gpu = resources["gpu-cuda-0"]
        assert gpu.tier == Tier.GPU
        assert gpu.concurrency == 1
        assert gpu.signature.platform == "cuda"

    def test_gpu_registered_with_gpu_hardware_and_no_backends(self, monkeypatch):
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._physical_cores", lambda: 8
        )

        backends = []
        catalog = _make_catalog(backends)
        hw = _hardware(gpu_type="nvidia")

        sched = build_scheduler(hw, catalog)

        resources = {r.name: r for r in sched._resources.values()}
        assert "gpu-cuda-0" in resources
        assert resources["gpu-cuda-0"].signature.platform == "cuda"

    def test_gpu_not_registered_without_backends_or_hardware(self, monkeypatch):
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._physical_cores", lambda: 8
        )

        backends = [
            _make_backend("sd-cpp-1", "sd-cpp", capabilities={"image-generation"}),
        ]
        catalog = _make_catalog(backends)
        hw = _hardware()

        sched = build_scheduler(hw, catalog)

        resources = {r.name: r for r in sched._resources.values()}
        assert "gpu-cuda-0" not in resources

    def test_gpu_apple_metal_platform(self, monkeypatch):
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._physical_cores", lambda: 8
        )

        backends = [
            _make_backend("mlx-1", "mlx", capabilities={"llm-chat"}),
        ]
        catalog = _make_catalog(backends)
        hw = _hardware(gpu_type="apple")

        sched = build_scheduler(hw, catalog)

        resources = {r.name: r for r in sched._resources.values()}
        gpu = resources["gpu-cuda-0"]
        assert gpu.signature.platform == "metal"
        assert gpu.signature.runtime == "native"

    def test_cpu_concurrency_derived_from_physical_cores(self, monkeypatch):
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._physical_cores", lambda: 8
        )

        catalog = _make_catalog([])
        hw = _hardware()

        sched = build_scheduler(hw, catalog)

        resources = {r.name: r for r in sched._resources.values()}
        cpu = resources["cpu-inference"]
        assert cpu.concurrency == max(1, min(8 // 2, 4))

    def test_cpu_concurrency_capped_at_four(self, monkeypatch):
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._physical_cores", lambda: 20
        )

        catalog = _make_catalog([])
        hw = _hardware()

        sched = build_scheduler(hw, catalog)

        resources = {r.name: r for r in sched._resources.values()}
        assert resources["cpu-inference"].concurrency == 4

    def test_score_cache_wired_for_all_resources(self, monkeypatch):
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._physical_cores", lambda: 8
        )

        score_cache = mock.Mock()
        score_cache.score.return_value = 0.95

        backends = [
            _make_backend(
                "rkllama-1", "rkllama",
                capabilities={"embedding"},
            ),
            _make_backend("vllm-1", "vllm", capabilities={"llm-chat"}),
            _make_backend("sd-cpp-1", "sd-cpp", capabilities={"image-generation"}),
            _make_backend("llama-cpp-1", "llama-cpp", capabilities={"llm-chat"}),
        ]
        catalog = _make_catalog(backends)
        hw = _hardware(gpu_type="cuda", npu_type="rknpu")

        sched = build_scheduler(hw, catalog, score_cache=score_cache)

        resources = {r.name: r for r in sched._resources.values()}
        assert resources["cpu-inference"].score_for("embedding") == 0.95
        assert resources["npu-rk3588"].score_for("embedding") == 0.95
        assert resources["gpu-cuda-0"].score_for("llm-chat") == 0.95

    def test_history_store_wired_to_scheduler(self, monkeypatch):
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._physical_cores", lambda: 8
        )

        history_store = mock.Mock()

        catalog = _make_catalog([])
        hw = _hardware()

        sched = build_scheduler(hw, catalog, history_store=history_store)

        assert sched._history_store is history_store

    def test_full_happy_path_registers_all_three_resources(self, monkeypatch):
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._physical_cores", lambda: 8
        )
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._probe_librknnrt_version",
            lambda: "1.3.0",
        )
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._probe_nvidia_vram",
            lambda: (8192, 16384),
        )

        backends = [
            _make_backend(
                "rkllama-1", "rkllama",
                capabilities={"image-generation", "embedding", "reranking"},
            ),
            _make_backend("vllm-1", "vllm", capabilities={"llm-chat"}),
            _make_backend("sd-cpp-1", "sd-cpp", capabilities={"image-generation"}),
            _make_backend("llama-cpp-1", "llama-cpp", capabilities={"llm-chat"}),
        ]
        catalog = _make_catalog(backends)
        hw = _hardware(gpu_type="cuda", npu_type="rknpu")

        sched = build_scheduler(hw, catalog)

        resources = {r.name: r for r in sched._resources.values()}
        assert "cpu-inference" in resources
        assert "npu-rk3588" in resources
        assert "gpu-cuda-0" in resources
        assert len(resources) == 3

    def test_cpu_capabilities_filter_sd_cpp_and_llama_cpp(self, monkeypatch):
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._physical_cores", lambda: 8
        )

        backends = [
            _make_backend("sd-cpp-1", "sd-cpp", capabilities={"image-generation"}),
            _make_backend("llama-cpp-1", "llama-cpp", capabilities={"llm-chat"}),
            _make_backend("ollama-1", "ollama", capabilities={"llm-chat"}),
        ]
        catalog = _make_catalog(backends)
        hw = _hardware()

        sched = build_scheduler(hw, catalog)

        resources = {r.name: r for r in sched._resources.values()}
        cpu = resources["cpu-inference"]
        assert cpu.capabilities == {"image-generation", "llm-chat"}

    def test_npu_capabilities_from_healthy_rkllama_backends(self, monkeypatch):
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._physical_cores", lambda: 8
        )
        monkeypatch.setattr(
            "tinyagentos.scheduler.discovery._probe_librknnrt_version",
            lambda: "1.3.0",
        )

        backends = [
            _make_backend(
                "rkllama-1", "rkllama", status="ok",
                capabilities={"image-generation", "embedding"},
            ),
            _make_backend(
                "rkllama-2", "rkllama", status="error",
                capabilities={"reranking"},
            ),
        ]
        catalog = _make_catalog(backends)
        hw = _hardware(npu_type="rknpu")

        sched = build_scheduler(hw, catalog)

        resources = {r.name: r for r in sched._resources.values()}
        npu = resources["npu-rk3588"]
        assert npu.capabilities == {"image-generation", "embedding"}
