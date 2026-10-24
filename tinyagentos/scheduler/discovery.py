"""Boot-time resource discovery.

Builds the initial set of Resources from the hardware profile and the
live backend catalog. Follows backend-driven discovery: a Resource is
registered only if the live catalog has at least one healthy backend
claiming the capabilities that Resource would serve.
"""
from __future__ import annotations

import logging
import os
import platform
from pathlib import Path
from typing import Optional

from tinyagentos.hardware import (
    METAL_RESOURCE_NAME,
    metal_available,
)
from tinyagentos.installers.mlx_installer import mlx_runtime_installed
from tinyagentos.scheduler.backend_catalog import BackendCatalog
from tinyagentos.scheduler.history_store import HistoryStore
from tinyagentos.scheduler.gpu_arbiter import _probe_nvidia_vram
from tinyagentos.scheduler.resource import Resource, Tier
from tinyagentos.scheduler.scheduler import Scheduler
from tinyagentos.scheduler.score_cache import ScoreCache
from tinyagentos.scheduler.types import ResourceSignature


def normalise_vram_probe(free_mb: int, total_mb: int) -> int:
    """Map a raw VRAM probe to a schedulable free-VRAM value.

    ``_probe_nvidia_vram()`` returns ``(free, total)``, and the ``total`` half is
    the signal that distinguishes two very different states a bare ``free``
    collapses together:

    * ``total_mb <= 0`` — the probe could not run at all (``nvidia-smi`` absent on
      AMD/ROCm, Apple Silicon, Rockchip, …).  This is *unknown*, not "full": fail
      OPEN with a large value so a non-NVIDIA host whose GPU resources are still
      registered (``discovery.py`` assigns runtime ``rocm``/``native``) is not
      permanently refused by ``Resource.can_admit()``'s
      ``avail < estimated_memory_mb + 1024`` check.
    * ``total_mb > 0 and free_mb <= 0`` — the probe ran and genuinely measured no
      free VRAM.  Fail CLOSED (report 0): the real "full / over-committed" case
      the scheduler must refuse, not inflate.

    A positive ``free_mb`` is used as-is.  (taOS #1992 M2.)

    Lives module-level so the fail-open/fail-closed split is unit-testable; the
    prior inline ``free if free > 0 else 999_999`` (always optimistic) and the
    first-cut ``free if free > 0 else 0`` (always pessimistic) each collapsed
    the unavailable-vs-full distinction in the wrong direction.
    """
    if total_mb <= 0:
        # Probe unavailable — fail OPEN, mirroring `_default_memory_probe`'s
        # "don't block on probe failure" convention (resource.py:185).
        return 999_999
    return free_mb if free_mb > 0 else 0



# Every capability a CPU can run given the right backend. CPU is the
# universal fallback, nothing is exclusive to GPU/NPU at the capability
# level, just faster on those devices. This set feeds the CPU resource's
# ``potential_capabilities`` so the UI shows latent coverage even when no
# CPU backend for that capability is currently loaded.
CPU_POTENTIAL_CAPABILITIES: set[str] = {
    "llm-chat",
    "embedding",
    "reranking",
    "image-generation",
    "speech-to-text",
    "text-to-speech",
    "vision",
}

# Every capability the RK3588 NPU can run given a suitable RKNN-exported
# model. Community ports exist for the full inference surface (LLMs and
# embeddings via rkllama, plus whisper / TTS / vision models on HuggingFace).
# The potential set matches the CPU's; ``capabilities`` (the live view) is
# still filtered to what's actually loaded on a backend right now.
NPU_RK3588_POTENTIAL_CAPABILITIES: set[str] = {
    "llm-chat",
    "embedding",
    "reranking",
    "image-generation",
    "speech-to-text",
    "text-to-speech",
    "vision",
}

# Every capability a GPU can run given the right backend. GPU is the
# fastest tier; the potential set mirrors CPU/NPU because any inference
# task that can run on CPU can also run (faster) on GPU. Live capabilities
# are still filtered to what backends actually have loaded right now.
GPU_POTENTIAL_CAPABILITIES: set[str] = {
    "llm-chat",
    "embedding",
    "reranking",
    "image-generation",
    "speech-to-text",
    "text-to-speech",
    "vision",
}

logger = logging.getLogger(__name__)


def _probe_librknnrt_version() -> str:
    """Read the librknnrt version string from the shared library."""
    candidates = [
        Path("/usr/lib/librknnrt.so"),
        Path("/usr/local/lib/librknnrt.so"),
        Path.home() / ".local" / "share" / "tinyagentos" / "rkllama" / "librknnrt.so",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = path.read_bytes()
            # The string "librknnrt version: X.Y.Z" is embedded in the binary.
            marker = b"librknnrt version: "
            idx = data.find(marker)
            if idx == -1:
                continue
            tail = data[idx + len(marker): idx + len(marker) + 32]
            version = tail.split(b" ", 1)[0].decode("ascii", errors="replace").strip("\x00")
            return version
        except Exception:
            continue
    return ""


def _physical_cores() -> int:
    try:
        import psutil
        return psutil.cpu_count(logical=False) or os.cpu_count() or 4
    except Exception:
        return os.cpu_count() or 4


def build_scheduler(
    hardware_profile,
    catalog: BackendCatalog,
    benchmark_store=None,
    score_cache: ScoreCache | None = None,
    history_store: HistoryStore | None = None,
) -> Scheduler:
    """Instantiate a Scheduler and register the resources the live catalog
    currently supports.

    Backend-driven: we only register a Resource class if the catalog has at
    least one healthy backend that would feed it. If the NPU backend is
    offline at startup, the `npu-rk3588` Resource is NOT registered and
    tasks fall through to `cpu-inference` until the backend returns.
    """
    scheduler = Scheduler(history_store=history_store)

    def _make_score_lookup(resource_name: str):
        """Return a sync score_lookup callable for this resource name.

        Reads from the ScoreCache if one is wired up, the cache is
        populated by a background polling task that pulls latest rows
        from the benchmark store every ~15s, keeping the scheduler's
        admission path sync-friendly without losing real data.
        """
        if score_cache is None:
            return None

        def _lookup(capability: str, model):
            return score_cache.score(resource_name, capability)

        return _lookup

    # NPU (RK3588), only if a healthy rkllama backend exists
    npu_backends = (
        catalog.backends_with_capability("image-generation")
        + catalog.backends_with_capability("embedding")
    )
    has_rk_backend = any(b.type == "rkllama" for b in npu_backends)
    npu_info = getattr(hardware_profile, "npu", None)
    npu_type = getattr(npu_info, "type", None)

    if has_rk_backend and npu_type == "rknpu":
        runtime_version = _probe_librknnrt_version()
        signature = ResourceSignature(
            platform="rk3588",
            runtime="librknnrt",
            runtime_version=runtime_version,
        )

        def _npu_capabilities() -> set[str]:
            caps: set[str] = set()
            for b in catalog.backends():
                if b.status == "ok" and b.type == "rkllama":
                    caps |= b.capabilities
            return caps

        def _npu_backend_for(capability: str) -> Optional[str]:
            for b in catalog.backends_with_capability(capability):
                if b.type == "rkllama":
                    return b.url
            return None

        # RK3588 has 3 NPU cores and rknn-toolkit supports multi-context
        # execution across them, rkllama already exploits this to hold
        # qwen3-embedding, qwen3-reranker, and qmd-query-expansion
        # simultaneously. So the Resource's concurrency is 3, NOT 1.
        # The image-gen UNet is the one case that wants exclusive use
        # because darkbit1001's lcm_server explicitly warns against
        # multi-core UNet execution; that's enforced separately by the
        # image-gen backend serialising its own /generate calls, not at
        # the scheduler level.
        scheduler.register(
            Resource(
                name="npu-rk3588",
                signature=signature,
                concurrency=3,
                tier=Tier.NPU,
                potential_capabilities=NPU_RK3588_POTENTIAL_CAPABILITIES,
                get_capabilities=_npu_capabilities,
                backend_lookup=_npu_backend_for,
                score_lookup=_make_score_lookup("npu-rk3588"),
            )
        )

    # GPU (CUDA/ROCm/Vulkan/Metal), only if a healthy GPU-capable backend exists.
    # GPU backends: vllm, llama-cpp (when built with CUDA), ollama (GPU mode),
    # exo, mlx (Apple Silicon GPU). Also sd-cpp/sd-gpu for image-generation.
    gpu_backend_types = {"vllm", "ollama", "exo", "mlx"}
    gpu_backends = [
        b for b in catalog.backends()
        if b.status == "ok" and b.type in gpu_backend_types
    ]
    gpu_info = getattr(hardware_profile, "gpu", None)
    gpu_type = getattr(gpu_info, "type", None) if gpu_info else None

    # Apple Silicon is not a CUDA host: MLX/Metal gets its own resource class,
    # `gpu-metal` (docs/design/resource-scheduler.md), because the name is what
    # lease ids, worker inventories and the UI key off. `metal_available()`
    # probes for the device rather than trusting the platform: a Mac reports
    # `gpu.type == "apple"` from its SoC, and an arm64 macOS VM has no Metal
    # device, so it must not register a GPU it cannot serve (taOS #329 review).
    # It also covers a Mac whose hardware probe failed to identify the GPU.
    # A host whose profile says `apple` but whose probe says no device keeps a
    # GPU-class resource only when a GPU backend is actually configured (that
    # branch is backend-driven, and an ollama in CPU mode must stay visible);
    # what it never gets is `gpu-metal`.
    is_metal = metal_available()
    if gpu_type == "apple" and not is_metal:
        logger.info(
            "discovery: this host reports Apple Silicon but exposes no Metal "
            "device (VM, or the graphics stack is down); no GPU resource",
        )
        gpu_type = None

    has_gpu_hardware = gpu_type not in (None, "", "none")

    if gpu_backends or has_gpu_hardware or is_metal:
        gpu_count = 1  # Default single-GPU; multi-GPU is Phase 2
        gpu_signature = ResourceSignature(
            platform="metal" if is_metal else (
                "cuda" if gpu_type in ("cuda", "nvidia") else (
                    "rocm" if gpu_type == "rocm" else "gpu"
                )
            ),
            runtime="cuda" if gpu_type in ("cuda", "nvidia") else (
                "rocm" if gpu_type == "rocm" else "native"
            ),
            runtime_version="",
        )
        if is_metal and not mlx_runtime_installed():
            logger.info(
                "discovery: %s is Apple Silicon without the pinned mlx-lm "
                "runtime installed; an mlx model install provisions it",
                METAL_RESOURCE_NAME,
            )

        def _gpu_vram_probe() -> int:
            free, total = _probe_nvidia_vram()
            # Split "probe unavailable" (fail open) from "probe ran, 0 free"
            # (fail closed). See normalise_vram_probe (M2 fix, taOS #1992).
            return normalise_vram_probe(free, total)

        def _gpu_capabilities() -> set[str]:
            caps: set[str] = set()
            for b in catalog.backends():
                if b.status == "ok" and b.type in gpu_backend_types:
                    caps |= b.capabilities
            return caps

        def _gpu_backend_for(capability: str):
            for b in catalog.backends_with_capability(capability):
                if b.type in gpu_backend_types:
                    return b.url
            return None

        for gpu_idx in range(gpu_count):
            gpu_name = METAL_RESOURCE_NAME if is_metal else f"gpu-cuda-{gpu_idx}"
            scheduler.register(
                Resource(
                    name=gpu_name,
                    signature=gpu_signature,
                    concurrency=1,
                    tier=Tier.GPU,
                    potential_capabilities=GPU_POTENTIAL_CAPABILITIES,
                    get_capabilities=_gpu_capabilities,
                    backend_lookup=_gpu_backend_for,
                    score_lookup=_make_score_lookup(gpu_name),
                    memory_probe=_gpu_vram_probe,
                )
            )
            logger.info(
                "discovery: registered GPU resource %s (%s, concurrency=1)",
                gpu_name,
                gpu_signature.platform,
            )

    # CPU inference, always register. Backend-driven: only advertises the
    # capabilities that some CPU backend currently serves (sd-cpp, llama-cpp, etc.)
    cpu_signature = ResourceSignature(
        platform=f"cpu-{platform.machine()}",
        runtime="native",
        runtime_version="",
    )

    def _cpu_capabilities() -> set[str]:
        caps: set[str] = set()
        for b in catalog.backends():
            if b.status != "ok":
                continue
            # CPU backends: sd-cpp, llama-cpp (local CPU mode), ollama (if no GPU)
            if b.type in ("sd-cpp", "llama-cpp"):
                caps |= b.capabilities
        return caps

    def _cpu_backend_for(capability: str) -> Optional[str]:
        for b in catalog.backends_with_capability(capability):
            if b.type in ("sd-cpp", "llama-cpp"):
                return b.url
        return None

    cpu_cores = _physical_cores()
    cpu_concurrency = max(1, min(cpu_cores // 2, 4))
    scheduler.register(
        Resource(
            name="cpu-inference",
            signature=cpu_signature,
            concurrency=cpu_concurrency,
            tier=Tier.CPU,
            potential_capabilities=CPU_POTENTIAL_CAPABILITIES,
            get_capabilities=_cpu_capabilities,
            backend_lookup=_cpu_backend_for,
            score_lookup=_make_score_lookup("cpu-inference"),
        )
    )

    return scheduler
