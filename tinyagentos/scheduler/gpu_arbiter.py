"""GPU Arbiter — VRAM-accounted admission + queue + eviction for GPU workloads.

Slice 2 of taOS #894 — builds on Slice 1 (VRAM endpoint) and the lease
system (#893). Provides admission control, queuing, and priority-based
eviction to prevent concurrent-load driver crashes (NVIDIA Xid 62).
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
    required_gpu_arch: str | None = field(default=None, compare=False)
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

        # --- TOCTOU-race fix (taOS #894 review feedback #2) ---
        # _check_admission reads live nvidia-smi free VRAM with no reservation.
        # Two concurrent submit_gpu calls can both pass before either model loads
        # — the exact concurrent-load crash the arbiter exists to prevent.
        # _reserved_vram_mb tracks in-flight admissions that haven't yet
        # consumed physical VRAM (model still loading).  _reserve_and_check()
        # atomically checks admission against (probe - _reserved_vram_mb) and
        # reserves the VRAM if admitted.  _release_reservation() is called in
        # _run_gpu_task's finally and in _evict_task.
        self._reserved_vram_mb: int = 0
        self._pending_reservations: dict[str, int] = {}
        self._reservation_lock = asyncio.Lock()

        self._queue_processor_task: asyncio.Task | None = None
        self._submitted = 0
        self._admitted = 0
        self._queued = 0
        self._evicted = 0
        self._dropped = 0
        # ── taOS #796: pause/resume queue control ────────────────────────
        self._paused: bool = False
        self._paused_at: float | None = None

    async def start(self) -> None:
        if self._queue_processor_task is not None:
            return
        self._paused = False
        self._queue_processor_task = asyncio.create_task(self._process_queue(), name="gpu-arbiter-queue")

    async def stop(self) -> None:
        if self._queue_processor_task is not None:
            self._queue_processor_task.cancel()
            try:
                await self._queue_processor_task
            except asyncio.CancelledError:
                pass
            self._queue_processor_task = None

    # ── taOS #796: pause/resume queue control ──────────────────────────

    @property
    def paused(self) -> bool:
        """Whether the queue processor is currently paused."""
        return self._paused

    def pause(self) -> bool:
        """Pause queue processing. New tasks still queue; running tasks finish.

        Returns True if paused, False if already paused.
        """
        if self._paused:
            return False
        self._paused = True
        self._paused_at = time.time()
        logger.info("gpu-arbiter: queue processing paused (queue_depth=%d, running=%d)",
                     self._queue.qsize(), len(self._running))
        return True

    def resume(self) -> bool:
        """Resume queue processing after a pause.

        Returns True if resumed, False if not paused.
        """
        if not self._paused:
            return False
        self._paused = False
        paused_for = time.time() - (self._paused_at or time.time())
        logger.info("gpu-arbiter: queue processing resumed (was paused for %.1fs, queue_depth=%d)",
                     paused_for, self._queue.qsize())
        self._paused_at = None
        return True

    # ── taOS #796: hardware-aware LLM admission ────────────────────────

    def _check_gpu_arch_compatibility(
        self, required_gpu_arch: str | None, resource_id: str | None = None,
    ) -> tuple[bool, str | None]:
        """Check if a worker's GPU architecture satisfies the requirement.

        ``required_gpu_arch`` is a CUDA compute-capability string like
        ``"sm_86"`` or ``"sm_75"``.  When the caller specifies this, the
        arbiter checks that at least one online cluster worker has a
        compatible GPU.  Without a cluster_manager, the check is skipped
        (standalone mode trusts the local GPU).

        Returns ``(True, None)`` if compatible or no arch requirement,
        ``(False, reason)`` if incompatible.
        """
        if not required_gpu_arch:
            return True, None
        if self._cluster_manager is None:
            return True, None

        for worker in self._cluster_manager.get_workers():
            if worker.status not in ("online", "draining"):
                continue
            gpu_info = worker.hardware.get("gpu", {}) if isinstance(worker.hardware, dict) else {}
            gpu_model = gpu_info.get("model", "") or ""
            cc = gpu_info.get("compute_cap", "") or ""
            if required_gpu_arch in gpu_model or required_gpu_arch in cc:
                if resource_id is None or resource_id.startswith(worker.name + ":"):
                    return True, None
        return False, f"no online worker with GPU architecture {required_gpu_arch}"

    # ── Reservation helpers (TOCTOU fix) ──────────────────────────────

    def _release_reservation(self, task_id: str) -> None:
        """Release an in-flight VRAM reservation for *task_id*.

        Idempotent — safe to call on a task that was never reserved (e.g.
        a zero-VRAM task or eviction of a queued-but-not-yet-admitted entry).
        """
        vram = self._pending_reservations.pop(task_id, None)
        if vram is not None:
            self._reserved_vram_mb -= vram
            logger.debug("gpu-arbiter: released reservation %d MiB for task %s", vram, task_id)

    async def _reserve_and_check(self, task_id: str, required_vram_mb: int) -> GpuAdmission:
        """Atomically check admission and reserve VRAM if admitted.

        Acquires _reservation_lock, calls _check_admission (which subtracts
        _reserved_vram_mb from the hardware probe), and if admitted
        immediately adds required_vram_mb to _reserved_vram_mb so
        concurrent callers see the reduced capacity.

        Returns the GpuAdmission.  The caller MUST call _release_reservation
        when the task finishes or is evicted.
        """
        if required_vram_mb <= 0:
            return GpuAdmission(admitted=True)

        async with self._reservation_lock:
            # Build a synthetic task just for the admission check.  The
            # reservation-aware check uses _reserved_vram_mb internally.
            admission = self._check_admission(None, required_vram_mb)
            if admission.admitted:
                self._reserved_vram_mb += required_vram_mb
                self._pending_reservations[task_id] = required_vram_mb
                logger.debug("gpu-arbiter: reserved %d MiB for task %s (total reserved: %d)",
                             required_vram_mb, task_id, self._reserved_vram_mb)
            return admission

    # ── Public API ────────────────────────────────────────────────────

    async def submit_gpu(
        self, task: Task, required_vram_mb: int = 0,
        evictable: bool = False, resource_id: str | None = None,
        required_gpu_arch: str | None = None,
    ) -> object:
        """Submit a GPU task with optional hardware-architecture requirements.

        Args:
            task: The Task to run.
            required_vram_mb: VRAM needed in MiB (0 = no VRAM check).
            evictable: Whether lower-priority tasks can be evicted for this.
            resource_id: Specific cluster resource to target.
            required_gpu_arch: CUDA compute capability required (e.g. ``"sm_86"``).
        """
        self._submitted += 1

        # Hardware architecture check (taOS #796)
        if required_gpu_arch:
            arch_ok, arch_reason = self._check_gpu_arch_compatibility(
                required_gpu_arch, resource_id,
            )
            if not arch_ok:
                raise NoResourceAvailableError(
                    f"GPU architecture requirement not met: {arch_reason} "
                    f"(task {task.id})"
                )

        if required_vram_mb > 0:
            admission = await self._reserve_and_check(task.id, required_vram_mb)
            if not admission.admitted:
                # Not admitted — release the reservation (belt-and-suspenders:
                # _reserve_and_check only reserves on admit, but be safe).
                self._release_reservation(task.id)
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
                    required_gpu_arch=required_gpu_arch,
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
            # Admitted — reservation already held by _reserve_and_check, so
            # _run_gpu_task can proceed safely.
        return await self._run_gpu_task(task, required_vram_mb, evictable, resource_id)

    def _check_admission(self, task: Task | None, required_vram_mb: int) -> GpuAdmission:
        """Check whether *required_vram_mb* can be admitted right now.

        Subtracts in-flight reservations (_reserved_vram_mb) from the
        hardware probe so that concurrent admission attempts see the
        capacity already promised to other tasks whose models are still
        loading.

        *task* may be None when called from _reserve_and_check (which
        doesn't need the full Task — just the VRAM budget).
        """
        if required_vram_mb <= 0:
            return GpuAdmission(admitted=True)
        free_vram, _total = self._vram_probe()
        # Subtract in-flight reservations from the probe to close the
        # TOCTOU window between two concurrent submit_gpu calls.
        effective_free = max(0, free_vram - self._reserved_vram_mb)
        if free_vram > 0:
            if effective_free < required_vram_mb:
                return GpuAdmission(
                    admitted=False,
                    free_vram_mb=effective_free,
                    required_vram_mb=required_vram_mb,
                    reason=(
                        f"insufficient local VRAM: need {required_vram_mb} MiB, "
                        f"have {effective_free} MiB available "
                        f"({free_vram} MiB free - {self._reserved_vram_mb} MiB reserved)"
                    ),
                )
            return GpuAdmission(
                admitted=True, free_vram_mb=effective_free,
                required_vram_mb=required_vram_mb,
            )
        if self._cluster_manager is not None:
            leases = self._cluster_manager.get_leases()
            for worker in self._cluster_manager.get_workers():
                if worker.status != "online":
                    continue
                worker_leases = sum(
                    l.required_vram_mb for l in leases
                    if l.resource_id.startswith(worker.name + ":") and l.required_vram_mb > 0
                )
                # Subtract in-flight reservations as well — the cluster-mode
                # path must also account for tasks whose models are still
                # loading (taOS #1705).
                available = worker.free_vram_mb - worker_leases - self._reserved_vram_mb
                if available >= required_vram_mb:
                    return GpuAdmission(
                        admitted=True,
                        free_vram_mb=available,
                        required_vram_mb=required_vram_mb,
                    )
            return GpuAdmission(
                admitted=False, required_vram_mb=required_vram_mb,
                reason=f"no cluster worker with {required_vram_mb} MiB free VRAM",
            )
        return GpuAdmission(admitted=True, required_vram_mb=required_vram_mb)

    async def _run_gpu_task(
        self, task: Task, required_vram_mb: int, evictable: bool, resource_id: str | None,
    ) -> object:
        """Execute a GPU task, holding a VRAM reservation for its duration.

        The caller (submit_gpu or _drain_queue) must have already reserved
        VRAM via _reserve_and_check.  This method releases the reservation
        in its finally block.

        The lease claim and _running registration are inside the try block
        so that the finally always releases the reservation, even when
        claim_lease fails (taOS #1705 — reservation leak fix).
        """
        lease_id: str | None = None
        try:
            if self._cluster_manager is not None and resource_id is not None:
                lease = await self._cluster_manager.claim_lease(
                    resource_id=resource_id, caller=task.submitter,
                    ttl_seconds=300, required_vram_mb=required_vram_mb,
                )
                if lease is None:
                    raise NoResourceAvailableError(
                        f"GPU lease claim failed for {resource_id} (task {task.id})"
                    )
                lease_id = lease.lease_id
            current = asyncio.current_task()
            async with self._running_lock:
                self._running[task.id] = (task, lease_id, int(task.priority), required_vram_mb)
                if current is not None:
                    self._running_tasks[task.id] = current
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
            # Release the VRAM reservation whether we completed, errored,
            # or were cancelled.  _evict_task handles its own reservation
            # release so idempotency matters.
            self._release_reservation(task.id)
            async with self._running_lock:
                entry = self._running.pop(task.id, None)
                self._running_tasks.pop(task.id, None)
            # Release the lease only for normal completion (not eviction).
            # When _evict_task evicts us it pops self._running first and
            # handles the lease — our pop above returns None, so we skip.
            if entry is not None:
                _task, _lid, _pri, _vram = entry
                if _lid is not None and self._cluster_manager is not None:
                    await self._cluster_manager.release_lease(_lid)

    async def cancel_running_for_leases(self, lease_ids: set[str]) -> int:
        """Cancel all running GPU tasks whose leases are in *lease_ids*.

        Called from ClusterManager drain paths when a worker's leases are
        force-released — the running GPU task must be evicted so its VRAM
        and asyncio resources are freed.

        Returns the number of tasks cancelled.
        """
        if not lease_ids:
            return 0
        victim_ids: list[str] = []
        async with self._running_lock:
            for task_id, (_task, lease_id, _pri, _vram) in self._running.items():
                if lease_id in lease_ids:
                    victim_ids.append(task_id)
        cancelled = 0
        for task_id in victim_ids:
            cancelled += await self._evict_task(task_id)
        if cancelled:
            logger.info("gpu-arbiter: cancelled %d running task(s) for %d drained lease(s)",
                         cancelled, len(lease_ids))
        return cancelled

    async def evict_lowest_priority(self, min_priority: int | None = None) -> int:
        if not self._eviction_enabled:
            return 0
        async with self._running_lock:
            victim_id, victim_priority = None, -1
            for tid, (_t, _lid, pri, _vram) in self._running.items():
                if min_priority is not None and pri < min_priority:
                    continue
                if pri > victim_priority:
                    victim_priority, victim_id = pri, tid
        if victim_id is None:
            return 0
        return await self._evict_task(victim_id)

    async def _evict_task(self, task_id: str) -> int:
        async with self._running_lock:
            if task_id not in self._running:
                return 0
            task, lease_id, _pri, _vram = self._running.pop(task_id)
            asyncio_task = self._running_tasks.pop(task_id, None)
        # Release the VRAM reservation — the task is being preempted and
        # its reserved VRAM should be returned to the pool immediately.
        self._release_reservation(task_id)
        if lease_id is not None and self._cluster_manager is not None:
            await self._cluster_manager.release_lease(lease_id)
        # Cancel the running asyncio Task — stops _run_gpu_task and frees VRAM
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
                if not self._paused:  # taOS #796: skip drain while paused
                    await self._drain_queue()
        except asyncio.CancelledError:
            raise

    async def _drain_queue(self) -> None:
        """Drain the admission queue one task at a time.

        Queue processing is intentionally serial — only one queued task is
        admitted per drain cycle to avoid flooding the GPU with concurrent
        loads.  The admitted task is spawned as a background asyncio Task so
        the drain loop can continue on the next tick and handle
        eviction-to-make-room for higher-priority arrivals.

        Uses _reserve_and_check so the queue processor doesn't race with
        concurrent submit_gpu calls — the reservation is atomic with the
        admission check (TOCTOU-safe).
        """
        retry: list[_QueuedGpuTask] = []
        drained = False
        while not self._queue.empty() and not drained:
            entry = self._queue.get_nowait()
            admission = await self._reserve_and_check(entry.task.id, entry.required_vram_mb)
            if not admission.admitted:
                # Try eviction-to-make-room for higher-priority queued tasks.
                # evict_lowest_priority(min_priority=N) skips running tasks
                # whose priority value is *lower* than N (i.e. tasks that are
                # actually higher priority), so only lower-or-equal priority
                # running tasks are candidates for eviction.
                evicted = await self.evict_lowest_priority(min_priority=int(entry.task.priority))
                if evicted > 0:
                    admission = await self._reserve_and_check(entry.task.id, entry.required_vram_mb)
            if admission.admitted:
                future = getattr(entry.task, "_arbiter_future", None)
                # Spawn as background task so drain doesn't block and
                # eviction-to-make-room stays responsive on subsequent ticks.
                t = asyncio.create_task(
                    self._run_gpu_task(entry.task, entry.required_vram_mb, entry.evictable, None),
                    name=f"gpu-arbiter-drain-{entry.task.id}",
                )

                # Wire result / exception from the background task back to the
                # submitter's _arbiter_future once it finishes (or fails).
                if future is not None:
                    def _propagate(ct: asyncio.Task, f: asyncio.Future = future) -> None:
                        if f.done():
                            return
                        if ct.cancelled():
                            return  # _evict_task already cancelled the future
                        exc = ct.exception()
                        if exc is not None:
                            f.set_exception(exc)
                        else:
                            f.set_result(ct.result())

                    t.add_done_callback(_propagate)

                drained = True
            else:
                # Not admitted — release reservation (idempotent) and retry later.
                self._release_reservation(entry.task.id)
                retry.append(entry)
        # Re-queue tasks that still can't be admitted
        for entry in retry:
            if not self._queue.full():
                self._queue.put_nowait(entry)
            else:
                self._dropped += 1
                future = getattr(entry.task, "_arbiter_future", None)
                if future is not None and not future.done():
                    future.set_exception(NoResourceAvailableError("queue full, task dropped"))

    async def stats(self) -> dict:
        async with self._running_lock:
            running_count = len(self._running)
        return {
            "submitted": self._submitted, "admitted": self._admitted,
            "queued": self._queued, "evicted": self._evicted,
            "dropped": self._dropped, "queue_depth": self._queue.qsize(),
            "running": running_count, "max_queue_size": self._max_queue_size,
            "eviction_enabled": self._eviction_enabled,
            "reserved_vram_mb": self._reserved_vram_mb,
            "pending_reservations": len(self._pending_reservations),
        }

    async def running_tasks(self) -> list[dict]:
        async with self._running_lock:
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
