"""GPU Arbiter — VRAM-accounted admission + queue + eviction for GPU workloads.

Slice 2 of taOS #894 — builds on Slice 1 (VRAM endpoint) and the lease
system (#893). Provides admission control, queuing, and priority-based
eviction to prevent concurrent-load driver crashes (NVIDIA Xid 62).

The VramReservationManager is the arbiter's INTERNAL reservation
bookkeeping — it tracks reserved VRAM per-resource with atomic
check-and-reserve so the arbiter is the single VRAM authority.
External callers go through GpuArbiter.submit_gpu, never through
VramReservationManager directly.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from tinyagentos.scheduler.types import (
    Capability,
    NoResourceAvailableError,
    Priority,
    Task,
)

logger = logging.getLogger(__name__)


# ── VRAM Reservation Manager (internal) ─────────────────────────────────


class VramReservationManager:
    """Internal VRAM reservation bookkeeping for the GpuArbiter.

    Tracks reserved VRAM per-resource with atomic check-and-reserve under
    an asyncio.Lock.  The arbiter uses this internally; external callers
    go through :meth:`GpuArbiter.submit_gpu`, never through this class
    directly.
    """

    def __init__(self, vram_probe: Callable[[], tuple[int, int]] | None = None):
        self._vram_probe = vram_probe or _default_vram_probe
        self._reserved: dict[str, int] = {}  # resource_key -> reserved_mb
        self._lock = asyncio.Lock()

    # ── sync helpers (best-effort, no lock) ──────────────────────────

    def available(self, resource_key: str = "local") -> int:
        """Best-effort available VRAM (probed minus reserved).

        Not atomic — for admission *checks* (the actual reserve happens
        later under the lock).  A stale read here is safe because
        :meth:`reserve` double-checks atomically.
        """
        free, _total = self._vram_probe()
        reserved = self._reserved.get(resource_key, 0)
        return max(0, free - reserved)

    def total_reserved(self, resource_key: str = "local") -> int:
        """Return currently reserved VRAM for *resource_key*."""
        return self._reserved.get(resource_key, 0)

    # ── async atomic operations ──────────────────────────────────────

    async def reserve(self, resource_key: str, vram_mb: int) -> bool:
        """Atomically check *and* reserve VRAM.  Returns ``True`` on success."""
        if vram_mb <= 0:
            return True
        async with self._lock:
            free, _total = self._vram_probe()
            reserved = self._reserved.get(resource_key, 0)
            if free - reserved >= vram_mb:
                self._reserved[resource_key] = reserved + vram_mb
                return True
            return False

    async def release(self, resource_key: str, vram_mb: int) -> None:
        """Release a reservation.  Idempotent — never goes below zero."""
        if vram_mb <= 0:
            return
        async with self._lock:
            current = self._reserved.get(resource_key, 0)
            self._reserved[resource_key] = max(0, current - vram_mb)

    def stats(self) -> dict:
        """Snapshot of reservation state (for observability)."""
        free, total = self._vram_probe()
        return {
            "free_vram_mb": free,
            "total_vram_mb": total,
            "reserved_by_resource": dict(self._reserved),
        }


def _default_vram_probe() -> tuple[int, int]:
    """No-GPU fallback: returns (0, 0)."""
    return 0, 0


def _probe_nvidia_vram() -> tuple[int, int]:
    """Probe free/total VRAM from nvidia-smi. Returns (free_mb, total_mb)."""
    try:
        import subprocess
        free_raw = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        total_raw = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        return int(free_raw.stdout.strip().split("\n")[0]), int(total_raw.stdout.strip().split("\n")[0])
    except Exception:
        return 0, 0


@dataclass(order=True)
class _QueuedGpuTask:
    """Internal queue entry, ordered by (priority, seq)."""
    priority: int
    seq: int
    task: Task = field(compare=False)
    required_vram_mb: int = field(compare=False)
    evictable: bool = field(compare=False)
    queued_at: float = field(default_factory=time.time, compare=False)


@dataclass
class GpuAdmission:
    """Result of a GPU admission check."""
    admitted: bool
    reason: str | None = None
    existing_lease_id: str | None = None
    existing_lease_holder: str | None = None
    free_vram_mb: int = 0
    required_vram_mb: int = 0


class GpuArbiter:
    """VRAM-accounted admission control layered on top of the Scheduler.

    Usage:
        arbiter = GpuArbiter(scheduler=sched, cluster_manager=cm)
        await arbiter.start()
        result = await arbiter.submit_gpu(task, required_vram_mb=4096)
        await arbiter.stop()

    ``submit_gpu`` is the **single public admission API** — all GPU work
    must route through it.  :class:`VramReservationManager` is internal
    bookkeeping; external callers never touch it directly.
    """

    def __init__(
        self,
        scheduler=None,
        cluster_manager=None,
        vram_probe: Callable[[], tuple[int, int]] | None = None,
        max_queue_size: int = 100,
        eviction_enabled: bool = True,
    ):
        self._scheduler = scheduler
        self._cluster_manager = cluster_manager
        self._vram_probe = vram_probe or _default_vram_probe
        self._max_queue_size = max_queue_size
        self._eviction_enabled = eviction_enabled
        self._queue: asyncio.PriorityQueue[_QueuedGpuTask] = asyncio.PriorityQueue(maxsize=max_queue_size)
        self._seq = 0
        self._running: dict[str, tuple[Task, str | None, int, int]] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._running_lock = asyncio.Lock()
        self._queue_processor_task: asyncio.Task | None = None
        self._submitted = 0
        self._admitted = 0
        self._queued = 0
        self._evicted = 0
        self._dropped = 0
        # Internal VRAM reservation bookkeeping — atomic check-and-reserve
        # so the arbiter is the single VRAM authority.  External callers
        # go through submit_gpu, never through this directly.
        self._vram_reservations = VramReservationManager(self._vram_probe)

    async def start(self) -> None:
        if self._queue_processor_task is not None:
            return
        self._queue_processor_task = asyncio.create_task(self._process_queue(), name="gpu-arbiter-queue")

    async def stop(self) -> None:
        if self._queue_processor_task is not None:
            self._queue_processor_task.cancel()
            try:
                await self._queue_processor_task
            except asyncio.CancelledError:
                pass
            self._queue_processor_task = None

    async def submit_gpu(
        self, task: Task, required_vram_mb: int = 0,
        evictable: bool = False, resource_id: str | None = None,
    ) -> object:
        self._submitted += 1
        if required_vram_mb > 0:
            admission = self._check_admission(task, required_vram_mb)
            if not admission.admitted:
                if self._queue.full():
                    self._dropped += 1
                    raise NoResourceAvailableError(
                        f"GPU arbiter queue full ({self._max_queue_size}), "
                        f"dropped task {task.id}"
                    )
                self._seq += 1
                entry = _QueuedGpuTask(
                    priority=int(task.priority), seq=self._seq, task=task,
                    required_vram_mb=required_vram_mb, evictable=evictable,
                )
                await self._queue.put(entry)
                self._queued += 1
                loop = asyncio.get_running_loop()
                done: asyncio.Future = loop.create_future()
                entry.task._arbiter_future = done  # type: ignore[attr-defined]
                try:
                    return await done
                except asyncio.CancelledError:
                    self._evicted += 1
                    raise
        return await self._run_gpu_task(task, required_vram_mb, evictable, resource_id)

    def _check_admission(self, task: Task, required_vram_mb: int) -> GpuAdmission:
        if required_vram_mb <= 0:
            return GpuAdmission(admitted=True)
        # Local GPU: check against probed VRAM minus reserved (best-effort
        # read; the actual reserve happens atomically in _run_gpu_task).
        free_vram = self._vram_reservations.available("local")
        _free_raw, total = self._vram_probe()
        if total > 0:
            if free_vram < required_vram_mb:
                return GpuAdmission(
                    admitted=False, free_vram_mb=free_vram, required_vram_mb=required_vram_mb,
                    reason=f"insufficient local VRAM: need {required_vram_mb} MiB, "
                           f"have {free_vram} MiB free ({self._vram_reservations.total_reserved('local')} MiB reserved)",
                )
            return GpuAdmission(admitted=True, free_vram_mb=free_vram, required_vram_mb=required_vram_mb)
        if self._cluster_manager is not None:
            leases = self._cluster_manager.get_leases()
            for worker in self._cluster_manager.get_workers():
                if worker.status != "online":
                    continue
                worker_leases = sum(
                    l.required_vram_mb for l in leases
                    if l.resource_id.startswith(worker.name + ":") and l.required_vram_mb > 0
                )
                if worker.free_vram_mb - worker_leases >= required_vram_mb:
                    return GpuAdmission(
                        admitted=True, free_vram_mb=worker.free_vram_mb - worker_leases,
                        required_vram_mb=required_vram_mb,
                    )
            return GpuAdmission(
                admitted=False, required_vram_mb=required_vram_mb,
                reason=f"no cluster worker with {required_vram_mb} MiB free VRAM",
            )
        return GpuAdmission(admitted=True, required_vram_mb=required_vram_mb)

    async def _run_gpu_task(self, task: Task, required_vram_mb: int, evictable: bool, resource_id: str | None) -> object:
        lease_id: str | None = None
        vram_reserved_local: bool = False

        if self._cluster_manager is not None and resource_id is not None:
            # Cluster path: claim a lease on the remote worker (VRAM checked by the lease system).
            lease = self._cluster_manager.claim_lease(
                resource_id=resource_id, caller=task.submitter,
                ttl_seconds=300, required_vram_mb=required_vram_mb,
            )
            if lease is None:
                raise NoResourceAvailableError(
                    f"GPU lease claim failed for {resource_id} (task {task.id})"
                )
            lease_id = lease.lease_id
        elif required_vram_mb > 0:
            # Local GPU path: atomically reserve VRAM through the reservation manager.
            if not await self._vram_reservations.reserve("local", required_vram_mb):
                raise NoResourceAvailableError(
                    f"GPU VRAM reservation failed: need {required_vram_mb} MiB, "
                    f"have {self._vram_reservations.available('local')} MiB available "
                    f"(task {task.id})"
                )
            vram_reserved_local = True

        current = asyncio.current_task()
        async with self._running_lock:
            self._running[task.id] = (task, lease_id, int(task.priority), required_vram_mb)
            if current is not None:
                self._running_tasks[task.id] = current
        try:
            if self._scheduler is not None:
                result = await self._scheduler.submit(task)
            else:
                result = await task.payload(None)
            self._admitted += 1
            return result
        except asyncio.CancelledError:
            logger.info("gpu-arbiter: task %s preempted via CancelledError (pri=%d, vram=%d)",
                         task.id, task.priority, required_vram_mb)
            raise
        finally:
            async with self._running_lock:
                entry = self._running.pop(task.id, None)
                self._running_tasks.pop(task.id, None)
            # Release the lease only for normal completion (not eviction).
            # When _evict_task evicts us it pops self._running first and
            # handles the lease — our pop above returns None, so we skip.
            if entry is not None:
                _task, _lid, _pri, _vram = entry
                if _lid is not None and self._cluster_manager is not None:
                    self._cluster_manager.release_lease(_lid)
            # Release local VRAM reservation on normal completion.
            # On eviction, _evict_task handles this via the reservation manager.
            if vram_reserved_local and entry is not None:
                await self._vram_reservations.release("local", required_vram_mb)

    def evict_lowest_priority(self, min_priority: int | None = None) -> int:
        if not self._eviction_enabled:
            return 0
        victim_id, victim_priority = None, -1
        for tid, (_t, _lid, pri, _vram) in self._running.items():
            if min_priority is not None and pri < min_priority:
                continue
            if pri > victim_priority:
                victim_priority, victim_id = pri, tid
        if victim_id is None:
            return 0
        return self._evict_task(victim_id)

    def _evict_task(self, task_id: str) -> int:
        # Atomically pop from _running — whoever pops first owns the lease
        # release.  If _run_gpu_task's finally beat us here the entry is
        # already gone; it will handle the lease itself for normal completion.
        entry = self._running.pop(task_id, None)
        if entry is None:
            return 0
        task, lease_id, _pri, _vram = entry
        # We are the sole lease releaser for evicted tasks.  _run_gpu_task's
        # finally block will see entry=None on its own pop and skip the release.
        if lease_id is not None and self._cluster_manager is not None:
            self._cluster_manager.release_lease(lease_id)
        else:
            # Local GPU task — release the VRAM reservation.  We cannot
            # await here (sync method), so schedule the release as a
            # background task that will run promptly on the event loop.
            if _vram > 0:
                asyncio.create_task(
                    self._vram_reservations.release("local", _vram),
                    name=f"vram-release-{task_id}",
                )
        # Cancel the running asyncio Task — stops _run_gpu_task and frees VRAM
        asyncio_task = self._running_tasks.pop(task_id, None)
        if asyncio_task is not None and not asyncio_task.done():
            asyncio_task.cancel()
        # Also cancel the arbiter future for queued tasks (belt-and-suspenders)
        future = getattr(task, "_arbiter_future", None)
        if future is not None and not future.done():
            future.cancel()
        self._evicted += 1
        logger.info("gpu-arbiter: evicted task %s (pri=%d, vram=%d, task_cancelled=%s)",
                     task_id, _pri, _vram, asyncio_task is not None)
        return 1

    async def _process_queue(self) -> None:
        try:
            while True:
                await asyncio.sleep(2)
                await self._drain_queue()
        except asyncio.CancelledError:
            raise

    async def _drain_queue(self) -> None:
        retry: list[_QueuedGpuTask] = []
        while not self._queue.empty():
            entry = self._queue.get_nowait()
            if self._check_admission(entry.task, entry.required_vram_mb).admitted:
                try:
                    result = await self._run_gpu_task(entry.task, entry.required_vram_mb, entry.evictable, None)
                    future = getattr(entry.task, "_arbiter_future", None)
                    if future is not None and not future.done():
                        future.set_result(result)
                except asyncio.CancelledError:
                    # Task was evicted by the arbiter — arbiter_future already
                    # cancelled by _evict_task, so the submitter got the signal.
                    # Just move on; the queue processor stays alive.
                    logger.debug("gpu-arbiter: drain cancelled — task %s evicted", entry.task.id)
                except Exception as exc:
                    future = getattr(entry.task, "_arbiter_future", None)
                    if future is not None and not future.done():
                        future.set_exception(exc)
                break
            else:
                retry.append(entry)
        for entry in retry:
            if not self._queue.full():
                self._queue.put_nowait(entry)
            else:
                self._dropped += 1
                future = getattr(entry.task, "_arbiter_future", None)
                if future is not None and not future.done():
                    future.set_exception(NoResourceAvailableError("queue full, task dropped"))

    def stats(self) -> dict:
        vram_stats = self._vram_reservations.stats()
        return {
            "submitted": self._submitted, "admitted": self._admitted,
            "queued": self._queued, "evicted": self._evicted,
            "dropped": self._dropped, "queue_depth": self._queue.qsize(),
            "running": len(self._running), "max_queue_size": self._max_queue_size,
            "eviction_enabled": self._eviction_enabled,
            "vram": vram_stats,
        }

    def running_tasks(self) -> list[dict]:
        return [
            {"task_id": tid, "capability": task.capability.value,
             "submitter": task.submitter, "priority": pri,
             "vram_mb": vram, "lease_id": lid}
            for tid, (task, lid, pri, vram) in self._running.items()
        ]

    def queue_snapshot(self) -> list[dict]:
        items: list[_QueuedGpuTask] = []
        while not self._queue.empty():
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        result = [
            {"task_id": e.task.id, "capability": e.task.capability.value,
             "priority": e.priority, "vram_mb": e.required_vram_mb,
             "queued_seconds": time.time() - e.queued_at}
            for e in items
        ]
        for e in items:
            if not self._queue.full():
                self._queue.put_nowait(e)
        return result
