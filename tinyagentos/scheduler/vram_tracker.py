"""VRAM tracker for GPU admission control (taOS #894 Slice 2)."""
from __future__ import annotations
import asyncio, logging, time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional
from tinyagentos.scheduler.types import Priority
logger = logging.getLogger(__name__)

@dataclass
class VramAllocation:
    task_id: str; model_id: str; vram_mb: int; priority: int
    created_at: float = field(default_factory=time.time); evictable: bool = True

class VramTracker:
    def __init__(self, total_vram_mb: int, headroom_mb: int = 1024,
                 evict_callback: Optional[Callable[[str, str], Awaitable[None]]] = None):
        self._total = total_vram_mb; self._headroom = headroom_mb
        self._evict_callback = evict_callback
        self._allocations: dict[str, VramAllocation] = {}
        self._lock = asyncio.Lock(); self._vram_freed = asyncio.Event()

    @property
    def total_vram_mb(self) -> int: return self._total

    @property
    def free_vram_mb(self) -> int:
        used = sum(a.vram_mb for a in self._allocations.values())
        return max(0, self._total - used - self._headroom)

    @property
    def used_vram_mb(self) -> int:
        return sum(a.vram_mb for a in self._allocations.values())

    @property
    def allocations(self) -> list[VramAllocation]:
        return sorted(self._allocations.values(), key=lambda a: a.priority, reverse=True)

    def can_admit(self, task_id: str, required_vram_mb: int) -> tuple[bool, Optional[str]]:
        if required_vram_mb <= 0: return True, None
        existing = self._allocations.get(task_id)
        used = self.used_vram_mb
        if existing is not None: used -= existing.vram_mb
        avail = self._total - used - self._headroom
        if avail >= required_vram_mb: return True, None
        return False, f"insufficient VRAM on GPU: need {required_vram_mb} MiB, have {avail} MiB free"

    def reserve(self, task_id: str, vram_mb: int, model_id: str = "",
                priority: int = Priority.INTERACTIVE_AGENT, evictable: bool = True) -> bool:
        if vram_mb <= 0: return True
        admitted, _ = self.can_admit(task_id, vram_mb)
        if not admitted: return False
        self._allocations[task_id] = VramAllocation(
            task_id=task_id, model_id=model_id, vram_mb=vram_mb, priority=priority, evictable=evictable)
        return True

    def release(self, task_id: str) -> Optional[VramAllocation]:
        alloc = self._allocations.pop(task_id, None)
        if alloc is not None: self._vram_freed.set()
        return alloc

    async def wait_for_vram(self) -> None:
        self._vram_freed.clear(); await self._vram_freed.wait()

    def find_eviction_candidates(self, incoming_priority: int, needed_vram_mb: int) -> list[VramAllocation]:
        candidates = [a for a in self._allocations.values()
                      if a.evictable and a.priority > incoming_priority]
        candidates.sort(key=lambda a: (-a.priority, a.created_at))
        freed, result = 0, []
        for alloc in candidates:
            result.append(alloc); freed += alloc.vram_mb
            if freed >= needed_vram_mb: break
        return result if freed >= needed_vram_mb else []

    async def evict_and_reserve(self, task_id: str, vram_mb: int, model_id: str = "",
                                priority: int = Priority.INTERACTIVE_AGENT, evictable: bool = True) -> bool:
        async with self._lock:
            if self.can_admit(task_id, vram_mb)[0]:
                return self.reserve(task_id, vram_mb, model_id, priority, evictable)
            candidates = self.find_eviction_candidates(priority, vram_mb)
            if not candidates: return False
            for alloc in candidates:
                self.release(alloc.task_id)
                if self._evict_callback is not None:
                    try: await self._evict_callback(alloc.task_id, alloc.model_id)
                    except Exception: logger.exception("VramTracker: evict callback failed")
            return self.reserve(task_id, vram_mb, model_id, priority, evictable)

    def stats(self) -> dict:
        return {"total_vram_mb": self._total, "headroom_mb": self._headroom,
                "free_vram_mb": self.free_vram_mb, "used_vram_mb": self.used_vram_mb,
                "allocations": len(self._allocations)}
