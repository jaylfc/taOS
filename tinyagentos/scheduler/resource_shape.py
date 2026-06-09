"""resource_shape.py -- per-backend resource shape declarations.

Each backend adapter describes the hardware dimensions it can use beyond
memory. The scheduler is backend-agnostic: it consults the shape rather
than hard-coding NPU or GPU knowledge.

Phase 1.5: RK3588 NPU is the first backend with a non-trivial parallel
core story. CUDA and CPU shapes are included for completeness and to
keep the scheduler interface uniform, but they carry no core/gpu_ids
semantics that require special handling in Phase 1.5.

Usage::

    shape = make_rk3588_npu_shape()
    # shape.cores == [0, 1, 2]
    # shape.has_cores() == True

    shape = make_cuda_shape(gpu_count=2)
    # shape.gpu_ids == [0, 1]
    # shape.has_cores() == False

    shape = make_cpu_shape()
    # shape.has_cores() == False
    # shape.has_gpu_ids() == False
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class BackendResourceShape:
    """Describes the hardware dimensions a backend can allocate.

    Fields are None when the dimension does not exist for this backend.

    Attributes:
        backend_type: canonical backend type string, e.g. "rkllama",
            "llama-cpp", "vllm", "ollama".
        cores: NPU core indices available to this backend. Only set
            for rkllama / rknn (RK3588). None for all other backends.
        gpu_ids: GPU device indices available. Only set for CUDA
            backends (llama-cpp/cuda, vllm). None for other backends.
        memory_mb: total memory budget in MB for this backend on the
            device. None means unconstrained or unknown.
    """

    backend_type: str
    cores: Optional[list[int]] = None         # [0, 1, 2] for RK3588 NPU
    gpu_ids: Optional[list[int]] = None       # [0, 1, ...] for CUDA
    memory_mb: Optional[int] = None           # total budget if known

    def has_cores(self) -> bool:
        """True when the backend exposes discrete NPU cores."""
        return self.cores is not None and len(self.cores) > 0

    def has_gpu_ids(self) -> bool:
        """True when the backend exposes discrete GPU device IDs."""
        return self.gpu_ids is not None and len(self.gpu_ids) > 0

    def available_core_count(self) -> int:
        """Number of NPU cores this backend can use, or 0."""
        return len(self.cores) if self.cores else 0

    def to_dict(self) -> dict:
        return {
            "backend_type": self.backend_type,
            "cores": self.cores,
            "gpu_ids": self.gpu_ids,
            "memory_mb": self.memory_mb,
        }


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def make_rk3588_npu_shape(memory_mb: Optional[int] = None) -> BackendResourceShape:
    """Resource shape for rkllama / rknn on the RK3588 NPU.

    Three physical compute cores (0, 1, 2). Tensor-parallel mode
    ('tp_mode') is chosen at load time and cannot be changed while the
    model is resident.
    """
    return BackendResourceShape(
        backend_type="rkllama",
        cores=[0, 1, 2],
        gpu_ids=None,
        memory_mb=memory_mb,
    )


def make_cuda_shape(
    gpu_count: int = 1,
    vram_mb: Optional[int] = None,
) -> BackendResourceShape:
    """Resource shape for llama.cpp (CUDA) or vllm.

    GPU IDs are 0-indexed up to gpu_count - 1. Phase 1.5 does not
    implement multi-GPU allocation; the shape is declared here so the
    scheduler interface is uniform and the data is available for Phase 2.
    """
    return BackendResourceShape(
        backend_type="llama-cpp-cuda",
        cores=None,
        gpu_ids=list(range(gpu_count)),
        memory_mb=vram_mb,
    )


def make_vllm_shape(
    gpu_count: int = 1,
    vram_mb: Optional[int] = None,
) -> BackendResourceShape:
    """Resource shape for vllm."""
    return BackendResourceShape(
        backend_type="vllm",
        cores=None,
        gpu_ids=list(range(gpu_count)),
        memory_mb=vram_mb,
    )


def make_cpu_shape(ram_mb: Optional[int] = None) -> BackendResourceShape:
    """Resource shape for llama.cpp (CPU-only)."""
    return BackendResourceShape(
        backend_type="llama-cpp-cpu",
        cores=None,
        gpu_ids=None,
        memory_mb=ram_mb,
    )


def make_ollama_shape(
    vram_mb: Optional[int] = None,
    ram_mb: Optional[int] = None,
) -> BackendResourceShape:
    """Resource shape for ollama.

    Ollama may run on GPU or CPU. Pass vram_mb for GPU-backed instances,
    ram_mb for CPU-only instances.
    """
    return BackendResourceShape(
        backend_type="ollama",
        cores=None,
        gpu_ids=None,
        memory_mb=vram_mb if vram_mb is not None else ram_mb,
    )


# ---------------------------------------------------------------------------
# Shape registry -- maps backend type string to a factory callable
# ---------------------------------------------------------------------------

_SHAPE_FACTORIES: dict[str, BackendResourceShape] = {}


def get_default_shape(backend_type: str) -> BackendResourceShape:
    """Return the default resource shape for a given backend type string.

    Used when a backend adapter has not explicitly overridden
    get_resource_shape(). Returns a memory-only shape for unknown types
    so the scheduler degenerates to the Phase 1 memory-only path.
    """
    mapping = {
        "rkllama": make_rk3588_npu_shape,
        "vllm": make_vllm_shape,
        "llama-cpp": make_cpu_shape,     # conservative default; caller can override
        "ollama": make_ollama_shape,
        "openai": make_cpu_shape,
        "anthropic": make_cpu_shape,
        "exo": make_cpu_shape,
        "mlx": make_cpu_shape,
        "sd-cpp": make_cpu_shape,
    }
    factory = mapping.get(backend_type)
    if factory is not None:
        return factory()
    # Unknown backend: safe memory-only fallback
    return BackendResourceShape(backend_type=backend_type)
