"""Resource, one physical accelerator (or CPU pool) that runs Tasks.

A Resource wraps a concurrency semaphore, a signature declaring its
platform / runtime / version, and a live view of the capabilities it
can serve (derived from the backends currently pointing at it). One
instance per physical device: ``npu-rk3588``, ``cpu-inference``,
``gpu-cuda-0``, ``gpu-cuda-1``, etc.

Resources do not own task queues in Phase 1, the Scheduler calls
``Resource.run(task)`` directly and the semaphore provides mutual
exclusion. Per-resource queueing with aging moves into Phase 2 when
multi-tier priority sharing is added.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable, Optional

import psutil

from tinyagentos.scheduler.types import ResourceSignature, Task

logger = logging.getLogger(__name__)


class Tier:
    """Static tier ranking for resources. Lower wins.

    Tiers let the scheduler prefer a faster class of hardware when both
    are available. GPU (CUDA/ROCm/Vulkan/Metal/MLX) is fastest, NPU
    second, CPU is the universal fallback. Cluster network resources
    (remote workers over HTTP) come last because they add round-trip
    latency on top of whatever the remote device's local tier is.
    """
    GPU = 0
    NPU = 1
    CPU = 2
    CLUSTER = 3


class Resource:
    """An accelerator or CPU pool. One instance per physical device.

    Args:
        name: stable id, "npu-rk3588", "cpu-inference", "gpu-cuda-0", ...
        signature: runtime identity for admission matching
        concurrency: how many Tasks can run in parallel
        tier: static tier ranking (see Tier class), lower is faster
        potential_capabilities: what this hardware class *could* run given
                                a suitable backend, even if no backend for
                                that capability is loaded right now. Used
                                by the UI to show latent capability and
                                by suggestion tooling.
        get_capabilities: callable returning the set of capabilities this
                          resource can serve *right now*, derived from the
                          live backend catalog (backend-driven).
        backend_lookup: callable that returns the live backend URL for a
                        given capability, used by payloads that need to
                        know where to POST the actual request
        score_lookup: optional callable that returns the latest benchmark
                      score for a (capability, model) pair. Scheduler uses
                      this alongside tier for smart routing. None if no
                      benchmark data is available yet.
        memory_probe: optional callable returning available RAM in MB
    """

    def __init__(
        self,
        name: str,
        signature: ResourceSignature,
        concurrency: int,
        get_capabilities: Callable[[], set[str]],
        backend_lookup: Callable[[str], Optional[str]],
        tier: int = Tier.CPU,
        potential_capabilities: Optional[set[str]] = None,
        score_lookup: Optional[Callable[[str, Optional[str]], Optional[float]]] = None,
        memory_probe: Optional[Callable[[], int]] = None,
    ):
        self.name = name
        self.signature = signature
        self.concurrency = concurrency
        self.tier = tier
        self._semaphore = asyncio.Semaphore(concurrency)
        self._in_flight = 0
        self._get_capabilities = get_capabilities
        self._backend_lookup = backend_lookup
        self._score_lookup = score_lookup
        self._potential = set(potential_capabilities or set())
        self._memory_probe = memory_probe or _default_memory_probe

    @property
    def capabilities(self) -> set[str]:
        """Live set of capabilities this resource can currently serve.

        Backend-driven: answers by asking the catalog, not a static field.
        Use this for admission decisions.
        """
        return self._get_capabilities()

    @property
    def potential_capabilities(self) -> set[str]:
        """Set of capabilities this resource *could* run given a suitable
        backend. For CPU this is typically all capabilities because
        CPU-only implementations exist for every inference task (just
        slower). For NPU/GPU it's what the hardware class supports.

        The union of potential and current capabilities gives the UI a
        full picture: 'ready now: [image-generation] · latent: [embedding,
        llm-chat, speech-to-text, ...]'.
        """
        return self._potential | self.capabilities

    def score_for(self, capability: str, model: Optional[str] = None) -> Optional[float]:
        """Latest benchmark score for this resource on the given capability.

        Returns None if no benchmark data exists yet, which is the common
        case on first boot, the scheduler then falls back to tier-only
        routing until benchmarks populate.
        """
        if self._score_lookup is None:
            return None
        try:
            return self._score_lookup(capability, model)
        except Exception:
            return None

    @property
    def in_flight(self) -> int:
        return self._in_flight

    def backend_url_for(self, capability: str) -> Optional[str]:
        """Look up the backend URL this resource uses for a given capability."""
        return self._backend_lookup(capability)

    def can_admit(self, task: Task) -> tuple[bool, Optional[str]]:
        """Check whether this resource can take a task right now.

        Returns ``(True, None)`` if yes, ``(False, reason)`` if not.
        """
        caps = self.capabilities
        if task.capability.value not in caps:
            return False, f"capability '{task.capability.value}' not served by {self.name}"
        for req in task.required_signatures:
            if not self.signature.matches(req):
                return False, (
                    f"signature mismatch: {self.name} is "
                    f"{self.signature.platform}/{self.signature.runtime}/{self.signature.runtime_version}, "
                    f"task requires {req.platform}/{req.runtime}/{req.runtime_version}"
                )
        if self._in_flight >= self.concurrency:
            return False, f"{self.name} is at concurrency cap ({self.concurrency})"
        if task.estimated_memory_mb > 0:
            avail = self._memory_probe()
            if avail < task.estimated_memory_mb + 1024:
                return False, (
                    f"insufficient memory on {self.name}: "
                    f"need {task.estimated_memory_mb} MB + 1024 MB headroom, "
                    f"have {avail} MB"
                )
        return True, None

    async def run(self, task: Task) -> tuple[object, float]:
        """Acquire the semaphore and run the task payload.

        Returns ``(result, elapsed_seconds)``. Exceptions propagate.
        """
        async with self._semaphore:
            self._in_flight += 1
            start = time.monotonic()
            try:
                result = await task.payload(self)
                elapsed = time.monotonic() - start
                return result, elapsed
            finally:
                self._in_flight -= 1


def _default_memory_probe() -> int:
    """Available RAM in MB."""
    try:
        return psutil.virtual_memory().available // (1024 * 1024)
    except Exception:
        return 999_999  # don't block on probe failure
