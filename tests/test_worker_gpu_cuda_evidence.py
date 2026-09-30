"""Worker resource inventory: ``gpu-cuda-0`` requires CUDA/ROCm evidence.

Follow-up to taOS #37 / PR #3238 (kanban card t_360eead2).  The worker used to
append ``gpu-cuda-0`` whenever any of the backends ``{vllm, ollama, exo, mlx}``
was running, on every platform that is not macOS.  That is not evidence of a
CUDA device: Ollama, vLLM, exo and mlx all serve in CPU mode on a host with no
NVIDIA/ROCm hardware (or with a driver the hardware probe could not see), so
CUDA-typed tasks -- and every A2A lease caller, whose default resource id is
``<worker>:gpu-cuda-0`` -- could be routed to a machine that cannot run them.
``scheduler/discovery.py`` already maps a non-CUDA/ROCm GPU to the generic
``gpu`` platform signature, so the resource name and the scheduler's own view
of the class disagreed.

Contract pinned here: ``gpu-cuda-0`` is advertised only when the hardware probe
reports a usable CUDA/ROCm runtime (``GpuInfo.cuda`` / ``GpuInfo.rocm``) *and* a
GPU-capable backend is live.

Red on pre-fix ``dev``: the register/heartbeat cases below observe
``["cpu-inference", "gpu-cuda-0"]`` for a CPU-only host running Ollama.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from unittest.mock import AsyncMock, Mock, patch

import pytest

from tinyagentos.hardware import GpuInfo, HardwareProfile
from tinyagentos.worker.agent import WorkerAgent


def _collapse_resources(backends: list[dict], hardware) -> list[str]:
    """Call the shipped helper (lazy import: it does not exist pre-fix)."""
    from tinyagentos.worker.agent import _collect_resources

    return _collect_resources(backends, hardware)


# Hardware profiles as ``_detect_gpu()`` builds them.
NO_ACCELERATOR = GpuInfo(type="none")
CUDA_DEVICE = GpuInfo(
    type="nvidia", model="NVIDIA GeForce RTX 4090", vram_mb=24576, cuda=True, vulkan=True
)
ROCM_DEVICE = GpuInfo(
    type="amd", model="AMD Radeon RX 7900 XTX", vram_mb=24576, rocm=True, vulkan=True
)
# Mali / Intel iGPU: a real GPU, usable through Vulkan, but no CUDA/ROCm runtime.
VULKAN_ONLY = GpuInfo(type="none", vulkan=True)
# NVIDIA card visible on the PCI bus with no kernel module loaded: the device
# exists, the CUDA runtime does not.
UNLOADED_NVIDIA = GpuInfo(type="nvidia", model="NVIDIA Corporation Device 2482")

OLLAMA = {"type": "ollama", "url": "http://127.0.0.1:11434", "status": "ok"}
# Manifest-synthetic entry: software declared in the worker manifest whose port
# is not answering (``detect_backends()`` appends these with url=None).
STOPPED_OLLAMA = {"type": "ollama", "url": None, "status": "stopped", "available_models": []}
STOPPED_RKLLAMA = {"type": "rkllama", "url": None, "status": "stopped", "available_models": []}


def _hw_dict(gpu: GpuInfo) -> dict:
    return asdict(HardwareProfile(ram_mb=16384, gpu=gpu))


class TestResourceInventoryContract:
    """The decision itself, per hardware class."""

    def test_a_cpu_only_linux_host_running_ollama_is_not_cuda_class(self):
        assert _collapse_resources([OLLAMA], _hw_dict(NO_ACCELERATOR)) == ["cpu-inference"]

    def test_cuda_evidence_advertises_the_gpu_class(self):
        assert _collapse_resources([OLLAMA], _hw_dict(CUDA_DEVICE)) == [
            "cpu-inference",
            "gpu-cuda-0",
        ]

    def test_rocm_evidence_advertises_the_gpu_class(self):
        # ``gpu-cuda-0`` is the name the scheduler gives every GPU resource
        # (discovery.py), ROCm included, so the ROCm equivalent keeps it.
        assert _collapse_resources([OLLAMA], _hw_dict(ROCM_DEVICE)) == [
            "cpu-inference",
            "gpu-cuda-0",
        ]

    def test_gpu_hardware_without_a_live_backend_stays_cpu_only(self):
        # Both halves of the contract still have to hold: the class is about a
        # *serving* accelerator, and the old backend probe was the other half.
        assert _collapse_resources([], _hw_dict(CUDA_DEVICE)) == ["cpu-inference"]

    def test_vulkan_only_gpu_is_not_advertised_as_cuda(self):
        assert _collapse_resources([OLLAMA], _hw_dict(VULKAN_ONLY)) == ["cpu-inference"]

    def test_nvidia_card_without_a_loaded_driver_is_not_evidence(self):
        assert _collapse_resources([OLLAMA], _hw_dict(UNLOADED_NVIDIA)) == ["cpu-inference"]

    def test_npu_class_is_unchanged(self):
        assert _collapse_resources([{"type": "rkllama", "url": "http://127.0.0.1:8080"}], _hw_dict(NO_ACCELERATOR)) == [
            "cpu-inference",
            "npu-rk3588",
        ]

    def test_a_stopped_backend_is_not_evidence(self):
        # detect_backends() appends manifest-synthetic status="stopped" entries
        # for declared-but-not-answering software; they must not resurrect an
        # accelerator class on a host that has the hardware but is not serving.
        assert _collapse_resources([STOPPED_OLLAMA], _hw_dict(CUDA_DEVICE)) == ["cpu-inference"]
        assert _collapse_resources([STOPPED_RKLLAMA], _hw_dict(NO_ACCELERATOR)) == ["cpu-inference"]

    def test_a_live_backend_beside_a_stopped_one_still_counts(self):
        assert _collapse_resources([STOPPED_RKLLAMA, OLLAMA], _hw_dict(CUDA_DEVICE)) == [
            "cpu-inference",
            "gpu-cuda-0",
        ]

    def test_a_backend_without_a_status_key_is_treated_as_live(self):
        # The type-only shape callers and tests use.
        assert _collapse_resources([{"type": "ollama"}], _hw_dict(CUDA_DEVICE)) == [
            "cpu-inference",
            "gpu-cuda-0",
        ]

    @pytest.mark.parametrize("hardware", [None, {}, {"gpu": None}, {"gpu": "weird"}])
    def test_missing_or_malformed_hardware_is_not_evidence(self, hardware):
        assert _collapse_resources([OLLAMA], hardware) == ["cpu-inference"]


def _capturing_client(captured: dict):
    """Return an ``httpx.AsyncClient`` stand-in that records the JSON payload.

    Built per call so the captured payload lives in the caller's local dict --
    a shared class attribute would let two concurrent tests overwrite each
    other's capture.
    """

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured["body"] = json.loads(kwargs["content"])
            # httpx's ``Response`` API is synchronous: ``raise_for_status()``
            # and ``json()`` are plain methods here (agent.py:665,669,814).
            # Mock them as such -- an AsyncMock hands back un-awaited
            # coroutines, which silently drops the generation-echo path into
            # its except branch and hides a real failure if production code
            # ever started awaiting them.
            resp = Mock()
            resp.status_code = 200
            resp.json.return_value = {"status": "ok", "generation": 1}
            return resp

    return _Client


_KV_SUPPORT = {"legacy": ["fp16"], "k": ["fp16"], "v": ["fp16"], "boundary": False}


async def _register(profile: HardwareProfile, backends: list[dict] | None = None) -> list[str]:
    captured: dict = {}
    agent = WorkerAgent(controller_url="http://controller:9000", name="test-agent", worker_port=9000)
    with patch("tinyagentos.worker.pairing.load_signing_key", return_value=b"fake-key"):
        with patch("httpx.AsyncClient", _capturing_client(captured)):
            with patch("tinyagentos.hardware.detect_hardware", return_value=profile):
                with patch.object(agent, "detect_backends", new_callable=AsyncMock, return_value=backends or [OLLAMA]):
                    with patch.object(agent, "detect_capabilities", return_value=["llm-chat"]):
                        with patch.object(agent, "detect_kv_quant_support", return_value=_KV_SUPPORT):
                            assert await agent.register() is True
    return captured["body"]["resources"]


async def _heartbeat(profile: HardwareProfile, backends: list[dict] | None = None) -> list[str]:
    captured: dict = {}
    agent = WorkerAgent(controller_url="http://controller:9000", name="test-agent", worker_port=9000)
    agent._registered = True
    with patch("tinyagentos.worker.pairing.load_signing_key", return_value=b"fake-key"):
        with patch("httpx.AsyncClient", _capturing_client(captured)):
            with patch("tinyagentos.hardware.detect_hardware", return_value=profile):
                with patch.object(agent, "detect_backends", new_callable=AsyncMock, return_value=backends or [OLLAMA]):
                    with patch.object(agent, "detect_capabilities", return_value=["llm-chat"]):
                        with patch.object(agent, "detect_kv_quant_support", return_value=_KV_SUPPORT):
                            with patch(
                                "tinyagentos.cluster.worker_capacity.capacity_snapshot",
                                return_value={"storage_cap_bytes": 0, "storage_used_bytes": 0, "bytes_deduped_total": 0},
                            ):
                                with patch(
                                    "tinyagentos.cluster.worker_capacity.gpu_vram_snapshot",
                                    return_value=None,
                                ):
                                    with patch("tinyagentos.worker.agent.psutil.cpu_percent", return_value=0.0):
                                        assert await agent.heartbeat() == 200
    return captured["body"]["resources"]


@pytest.mark.asyncio
class TestWorkerRegistrationInventory:
    """End-to-end: what actually lands in the registration payload."""

    async def test_register_omits_gpu_cuda_on_a_cpu_only_host(self):
        assert await _register(HardwareProfile(ram_mb=16384, gpu=NO_ACCELERATOR)) == ["cpu-inference"]

    async def test_register_keeps_gpu_cuda_with_evidence(self):
        assert await _register(HardwareProfile(ram_mb=16384, gpu=CUDA_DEVICE)) == [
            "cpu-inference",
            "gpu-cuda-0",
        ]

    async def test_register_ignores_a_stopped_backend_on_a_cuda_host(self):
        assert await _register(
            HardwareProfile(ram_mb=16384, gpu=CUDA_DEVICE), backends=[STOPPED_OLLAMA]
        ) == ["cpu-inference"]


@pytest.mark.asyncio
class TestWorkerHeartbeatInventory:
    """End-to-end: what actually lands in the heartbeat payload."""

    async def test_heartbeat_omits_gpu_cuda_on_a_cpu_only_host(self):
        assert await _heartbeat(HardwareProfile(ram_mb=16384, gpu=NO_ACCELERATOR)) == ["cpu-inference"]

    async def test_heartbeat_keeps_gpu_cuda_with_evidence(self):
        assert await _heartbeat(HardwareProfile(ram_mb=16384, gpu=CUDA_DEVICE)) == [
            "cpu-inference",
            "gpu-cuda-0",
        ]

    async def test_heartbeat_ignores_a_stopped_backend_on_a_cuda_host(self):
        # Twin of the register case: the heartbeat rebuilds the inventory on
        # every tick, so a backend that stops between ticks must drop the class
        # here too, not just at registration.
        assert await _heartbeat(
            HardwareProfile(ram_mb=16384, gpu=CUDA_DEVICE), backends=[STOPPED_OLLAMA]
        ) == ["cpu-inference"]


class _EmptyPath:
    """Stand-in for ``pathlib.Path`` that reports every path as absent.

    Keeps the ``_detect_gpu()`` pin below independent of the host it runs on:
    without it, a real ``/sys/class/misc/mali0`` or a panfrost ``/sys/class/drm``
    entry would change the result on an ARM dev box.
    """

    def __init__(self, *args, **kwargs):
        pass

    def exists(self):
        return False

    def glob(self, *args, **kwargs):
        return []

    def read_text(self, *args, **kwargs):
        return ""

    def resolve(self):
        return self

    @property
    def name(self):
        return ""


class TestProbeToResourceMapping:
    """Pin the ``_detect_gpu()`` -> resource-name mapping the card asked for."""

    def test_bare_linux_host_probe_result_is_not_cuda_evidence(self, monkeypatch):
        from tinyagentos import hardware as hardware_module

        monkeypatch.setattr("platform.system", lambda: "Linux")
        # No NVIDIA kernel module, no nvidia-smi, nothing GPU-ish on the PCI bus.
        monkeypatch.setattr(hardware_module, "_detect_nvidia_via_proc", lambda: ("", False))
        monkeypatch.setattr(hardware_module, "_run", lambda *args, **kwargs: "")
        monkeypatch.setattr(hardware_module.shutil, "which", lambda name: None)
        monkeypatch.setattr(hardware_module, "Path", _EmptyPath)

        gpu = hardware_module._detect_gpu()

        assert gpu.type == "none"
        assert gpu.cuda is False
        assert gpu.rocm is False
        # ...and that is exactly the shape the worker treats as no evidence.
        assert _collapse_resources([OLLAMA], {"gpu": asdict(gpu)}) == ["cpu-inference"]
