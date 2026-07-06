"""Resource, one physical accelerator (or CPU pool) that runs Tasks.

GPU VRAM admission (taOS #894 Slice 2): GPU-tier resources may carry
an optional VramTracker. can_admit() checks VRAM budget, and run()
reserves VRAM (with eviction + wait loop) before execution.
"""
from __future__ import annotations
import asyncio, logging, time
from typing import Awaitable, Callable, Optional
import psutil
from tinyagentos.scheduler.types import ResourceSignature, Task
logger = logging.getLogger(__name__)

class Tier:
    GPU = 0; NPU = 1; CPU = 2; CLUSTER = 3

class Resource:
    def __init__(self, name: str, signature: ResourceSignature, concurrency: int,
                 get_capabilities: Callable[[], set[str]],
                 backend_lookup: Callable[[str], Optional[str]],
                 tier: int = Tier.CPU,
                 potential_capabilities: Optional[set[str]] = None,
                 score_lookup: Optional[Callable[[str, Optional[str]], Optional[float]]] = None,
                 memory_probe: Optional[Callable[[], int]] = None,
                 vram_tracker=None):
        self.name = name; self.signature = signature; self.concurrency = concurrency
        self.tier = tier; self._semaphore = asyncio.Semaphore(concurrency)
        self._in_flight = 0; self._get_capabilities = get_capabilities
        self._backend_lookup = backend_lookup; self._score_lookup = score_lookup
        self._potential = set(potential_capabilities or set())
        self._memory_probe = memory_probe or _default_memory_probe
        self.vram_tracker = vram_tracker

    @property
    def capabilities(self) -> set[str]: return self._get_capabilities()

    @property
    def potential_capabilities(self) -> set[str]: return self._potential | self.capabilities

    def score_for(self, capability: str, model: Optional[str] = None) -> Optional[float]:
        if self._score_lookup is None: return None
        try: return self._score_lookup(capability, model)
        except Exception: return None

    @property
    def in_flight(self) -> int: return self._in_flight

    def backend_url_for(self, capability: str) -> Optional[str]: return self._backend_lookup(capability)

    def can_admit(self, task: Task) -> tuple[bool, Optional[str]]:
        caps = self.capabilities
        if task.capability.value not in caps:
            return False, f"capability '{task.capability.value}' not served by {self.name}"
        for req in task.required_signatures:
            if not self.signature.matches(req):
                return False, f"signature mismatch: {self.name}"
        if self._in_flight >= self.concurrency:
            return False, f"{self.name} is at concurrency cap ({self.concurrency})"
        if task.estimated_memory_mb > 0:
            avail = self._memory_probe()
            if avail < task.estimated_memory_mb + 1024:
                return False, f"insufficient memory on {self.name}"
        if self.vram_tracker is not None and task.estimated_vram_mb > 0:
            ok, reason = self.vram_tracker.can_admit(task.id, task.estimated_vram_mb)
            if not ok: return False, reason
        return True, None

    async def run(self, task: Task) -> tuple[object, float]:
        tracker = self.vram_tracker
        if tracker is not None and task.estimated_vram_mb > 0:
            while True:
                reserved = await tracker.evict_and_reserve(
                    task_id=task.id, vram_mb=task.estimated_vram_mb,
                    model_id=getattr(task, 'model_id', ''),
                    priority=int(task.priority))
                if reserved: break
                await tracker.wait_for_vram()
        async with self._semaphore:
            self._in_flight += 1
            start = time.monotonic()
            try:
                result = await task.payload(self)
                return result, time.monotonic() - start
            finally:
                self._in_flight -= 1
                if tracker is not None and task.estimated_vram_mb > 0:
                    tracker.release(task.id)

def _default_memory_probe() -> int:
    try: return psutil.virtual_memory().available // (1024 * 1024)
    except Exception: return 999_999
