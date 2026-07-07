"""GPU Arbiter — VRAM-accounted admission + queue + eviction for GPU workloads.

Provides admission control, queuing, and priority-based eviction for GPU
inference tasks. Coordinates with the scheduler drain mechanism: when
the arbiter decides to evict a model, it notifies the scheduler drain so
it stops routing new work, then waits for a drain-complete callback
before proceeding with the actual eviction.

taOS #1707: drain→arbiter wiring.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from tinyagentos.scheduler.types import (
    NoResourceAvailableError,
    Task,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# VRAM probe helpers
# ---------------------------------------------------------------------------

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
        return (
            int(free_raw.stdout.strip().split("\n")[0]),
            int(total_raw.stdout.strip().split("\n")[0]),
        )
    except Exception:
        return 0, 0


# ---------------------------------------------------------------------------
# Internal queue entry
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Admission result
# ---------------------------------------------------------------------------

@dataclass
class GpuAdmission:
    """Result of a GPU admission check."""
    admitted: bool
    reason: str | None = None
    free_vram_mb: int = 0
    required_vram_mb: int = 0


# ---------------------------------------------------------------------------
# Drain state tracking
# ---------------------------------------------------------------------------

@dataclass
class _DrainState:
    """Tracks an active drain for a model being evicted.

    The arbiter sets this when it decides to evict and calls
    drain_notify_fn.  The scheduler drain path calls
    arbiter.on_drain_complete(model_id) when draining is finished,
    which signals the event so the arbiter can proceed with eviction.
    """
    model_id: str
    event: asyncio.Event = field(default_factory=asyncio.Event)
    started_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# GPU Arbiter
# ---------------------------------------------------------------------------

class GpuArbiter:
    """VRAM-accounted admission control with scheduler drain coordination.

    Layered on top of the Scheduler. Provides admission control, queuing,
    and priority-based eviction. Coordinates with the scheduler drain
    mechanism via drain_notify_fn and on_drain_complete.

    Usage::

        arbiter = GpuArbiter(
            scheduler=sched,
            cluster_manager=cm,
            drain_notify_fn=sched.drain_model,
        )
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
        drain_notify_fn: Optional[Callable[[str], object]] = None,
        drain_timeout: float = 60.0,
    ):
        self._scheduler = scheduler
        self._cluster_manager = cluster_manager
        self._vram_probe = vram_probe or _default_vram_probe
        self._max_queue_size = max_queue_size
        self._eviction_enabled = eviction_enabled

        # ── drain→arbiter wiring (taOS #1707) ──────────────────────────
        # drain_notify_fn(model_id) is called when the arbiter decides to
        # evict a model. The scheduler should stop routing new work to the
        # model and call arbiter.on_drain_complete(model_id) when done.
        self._drain_notify_fn = drain_notify_fn
        self._drain_timeout = drain_timeout
        # model_id → _DrainState for active drains
        self._draining: dict[str, _DrainState] = {}

        self._queue: asyncio.PriorityQueue[_QueuedGpuTask] = (
            asyncio.PriorityQueue(maxsize=max_queue_size)
        )
        self._seq = 0
        self._running: dict[str, tuple[Task, str | None, int, int]] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._running_lock = asyncio.Lock()

        # TOCTOU-race fix: in-flight VRAM reservation tracking
        self._reserved_vram_mb: int = 0
        self._pending_reservations: dict[str, int] = {}
        self._reservation_lock = asyncio.Lock()

        self._queue_processor_task: asyncio.Task | None = None
        self._submitted = 0
        self._admitted = 0
        self._queued = 0
        self._evicted = 0
        self._dropped = 0
        self._paused: bool = False
        self._paused_at: float | None = None

    # ── Lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        if self._queue_processor_task is not None:
            return
        self._paused = False
        self._queue_processor_task = asyncio.create_task(
            self._process_queue(), name="gpu-arbiter-queue"
        )

    async def stop(self) -> None:
        if self._queue_processor_task is not None:
            self._queue_processor_task.cancel()
            try:
                await self._queue_processor_task
            except asyncio.CancelledError:
                pass
            self._queue_processor_task = None

    # ── Pause / resume ─────────────────────────────────────────────────

    @property
    def paused(self) -> bool:
        return self._paused

    def pause(self) -> bool:
        if self._paused:
            return False
        self._paused = True
        self._paused_at = time.time()
        logger.info(
            "gpu-arbiter: queue processing paused (queue_depth=%d, running=%d)",
            self._queue.qsize(), len(self._running),
        )
        return True

    def resume(self) -> bool:
        if not self._paused:
            return False
        self._paused = False
        paused_for = time.time() - (self._paused_at or time.time())
        logger.info(
            "gpu-arbiter: queue processing resumed (was paused for %.1fs, queue_depth=%d)",
            paused_for, self._queue.qsize(),
        )
        self._paused_at = None
        return True

    # ── drain→arbiter wiring (taOS #1707) ──────────────────────────────

    async def on_drain_complete(self, model_id: str) -> bool:
        """Called by the scheduler drain path when draining is finished.

        Signals the waiting arbiter that it can now proceed with eviction
        for *model_id*. Returns False if no drain was in progress for
        this model (stale / duplicate callback).
        """
        state = self._draining.pop(model_id, None)
        if state is None:
            logger.debug(
                "gpu-arbiter: on_drain_complete for %r — no active drain (stale callback)",
                model_id,
            )
            return False
        elapsed = time.time() - state.started_at
        logger.info(
            "gpu-arbiter: drain complete for %r (waited %.1fs)",
            model_id, elapsed,
        )
        state.event.set()
        return True

    async def _notify_drain_and_wait(self, model_id: str) -> bool:
        """Notify the scheduler to drain *model_id* and wait for completion.

        Calls drain_notify_fn(model_id) if configured, then waits for
        on_drain_complete(model_id) to be called (up to _drain_timeout).

        Returns True if drain completed, False if timed out or no drain
        function was configured.
        """
        if self._drain_notify_fn is None:
            # No drain coordination configured — proceed immediately.
            return True

        # Already draining this model? Wait on the existing drain.
        existing = self._draining.get(model_id)
        if existing is not None:
            logger.debug(
                "gpu-arbiter: %r already draining, waiting on existing drain",
                model_id,
            )
            try:
                await asyncio.wait_for(
                    existing.event.wait(), timeout=self._drain_timeout,
                )
                return True
            except asyncio.TimeoutError:
                logger.warning(
                    "gpu-arbiter: drain timeout for %r (%.0fs), proceeding with eviction",
                    model_id, self._drain_timeout,
                )
                self._draining.pop(model_id, None)
                return False

        state = _DrainState(model_id=model_id)
        self._draining[model_id] = state

        logger.info("gpu-arbiter: notifying scheduler drain for %r", model_id)
        try:
            result = self._drain_notify_fn(model_id)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception(
                "gpu-arbiter: drain_notify_fn failed for %r, proceeding with eviction",
                model_id,
            )
            self._draining.pop(model_id, None)
            return False

        try:
            await asyncio.wait_for(
                state.event.wait(), timeout=self._drain_timeout,
            )
            return True
        except asyncio.TimeoutError:
            logger.warning(
                "gpu-arbiter: drain timeout for %r (%.0fs), proceeding with eviction anyway",
                model_id, self._drain_timeout,
            )
            self._draining.pop(model_id, None)
            return False

    # ── VRAM reservation helpers ───────────────────────────────────────

    def _release_reservation(self, task_id: str) -> None:
        """Release an in-flight VRAM reservation for *task_id*.

        Idempotent — safe to call on a task that was never reserved.
        """
        vram = self._pending_reservations.pop(task_id, None)
        if vram is not None:
            self._reserved_vram_mb -= vram
            logger.debug(
                "gpu-arbiter: released reservation %d MiB for task %s",
                vram, task_id,
            )

    async def _reserve_and_check(
        self, task_id: str, required_vram_mb: int,
    ) -> GpuAdmission:
        """Atomically check admission and reserve VRAM if admitted."""
        if required_vram_mb <= 0:
            return GpuAdmission(admitted=True)

        async with self._reservation_lock:
            admission = self._check_admission(required_vram_mb)
            if admission.admitted:
                self._reserved_vram_mb += required_vram_mb
                self._pending_reservations[task_id] = required_vram_mb
                logger.debug(
                    "gpu-arbiter: reserved %d MiB for task %s (total reserved: %d)",
                    required_vram_mb, task_id, self._reserved_vram_mb,
                )
            return admission

    # ── Public API ─────────────────────────────────────────────────────

    async def submit_gpu(
        self,
        task: Task,
        required_vram_mb: int = 0,
        evictable: bool = False,
        resource_id: str | None = None,
        required_gpu_arch: str | None = None,
    ) -> object:
        """Submit a GPU task with optional hardware-architecture requirements."""
        self._submitted += 1

        if required_vram_mb > 0:
            admission = await self._reserve_and_check(task.id, required_vram_mb)
            if not admission.admitted:
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
        return await self._run_gpu_task(task, required_vram_mb, evictable, resource_id)

    def _check_admission(self, required_vram_mb: int) -> GpuAdmission:
        """Check whether *required_vram_mb* can be admitted right now.

        Subtracts in-flight reservations (_reserved_vram_mb) from the
        hardware probe to close the TOCTOU window.
        """
        if required_vram_mb <= 0:
            return GpuAdmission(admitted=True)

        free_vram, _total = self._vram_probe()
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

        # No local VRAM probe — check cluster workers
        if self._cluster_manager is not None:
            leases = self._cluster_manager.get_leases()
            for worker in self._cluster_manager.get_workers():
                if worker.status != "online":
                    continue
                worker_leases = sum(
                    l.required_vram_mb for l in leases
                    if l.resource_id.startswith(worker.name + ":")
                    and l.required_vram_mb > 0
                )
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

    # ── Task execution ─────────────────────────────────────────────────

    async def _run_gpu_task(
        self,
        task: Task,
        required_vram_mb: int,
        evictable: bool,
        resource_id: str | None,
    ) -> object:
        """Execute a GPU task, holding a VRAM reservation for its duration."""
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
                self._running[task.id] = (
                    task, lease_id, int(task.priority), required_vram_mb,
                )
                if current is not None:
                    self._running_tasks[task.id] = current
            if self._scheduler is not None:
                result = await self._scheduler.submit(task)
            else:
                result = await task.payload(None)
            self._admitted += 1
            return result
        except asyncio.CancelledError:
            logger.info(
                "gpu-arbiter: task %s preempted via CancelledError (pri=%d, vram=%d)",
                task.id, task.priority, required_vram_mb,
            )
            raise
        finally:
            self._release_reservation(task.id)
            async with self._running_lock:
                entry = self._running.pop(task.id, None)
                self._running_tasks.pop(task.id, None)
            if entry is not None:
                _task, _lid, _pri, _vram = entry
                if _lid is not None and self._cluster_manager is not None:
                    await self._cluster_manager.release_lease(_lid)

    # ── Eviction ───────────────────────────────────────────────────────

    async def cancel_running_for_leases(self, lease_ids: set[str]) -> tuple[int, int]:
        """Cancel all running GPU tasks whose leases are in *lease_ids*.

        Called from ClusterManager drain paths when a worker's leases are
        force-released.

        Returns (cancelled, already_completed).
        """
        if not lease_ids:
            return 0, 0
        victim_ids: list[str] = []
        async with self._running_lock:
            for task_id, (_task, lease_id, _pri, _vram) in self._running.items():
                if lease_id in lease_ids:
                    victim_ids.append(task_id)
        cancelled = 0
        already_completed = 0
        for task_id in victim_ids:
            if await self._evict_task(task_id):
                cancelled += 1
            else:
                already_completed += 1
        if cancelled or already_completed:
            logger.info(
                "gpu-arbiter: drain cancelled %d, already-done %d (leases=%d)",
                cancelled, already_completed, len(lease_ids),
            )
        return cancelled, already_completed

    async def evict_lowest_priority(self, min_priority: int | None = None) -> int:
        """Evict the running task with the highest numeric priority value.

        Higher numeric priority = lower actual priority = evicted first.
        If *min_priority* is given, only tasks with priority >= min_priority
        are considered.
        """
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
        """Evict a running task, coordinating with the scheduler drain.

        taOS #1707: Before cancelling the running task, notifies the
        scheduler drain so it stops routing new work, and waits for
        the drain-complete callback before proceeding.
        """
        async with self._running_lock:
            if task_id not in self._running:
                return 0
            task, lease_id, _pri, _vram = self._running.pop(task_id)
            asyncio_task = self._running_tasks.pop(task_id, None)

        # ── drain→arbiter wiring (taOS #1707) ──────────────────────
        # Notify the scheduler to drain this model and wait for completion.
        model_id = getattr(task, "model_id", None) or task.id
        await self._notify_drain_and_wait(model_id)

        # Release VRAM reservation
        self._release_reservation(task_id)

        # Release cluster lease
        if lease_id is not None and self._cluster_manager is not None:
            await self._cluster_manager.release_lease(lease_id)

        # Cancel the running asyncio Task
        if asyncio_task is not None and not asyncio_task.done():
            asyncio_task.cancel()

        # Also cancel the arbiter future for queued tasks
        future = getattr(task, "_arbiter_future", None)
        if future is not None and not future.done():
            future.cancel()

        self._evicted += 1
        logger.info(
            "gpu-arbiter: evicted task %s (pri=%d, vram=%d, task_cancelled=%s)",
            task_id, _pri, _vram, asyncio_task is not None,
        )
        return 1

    # ── Queue processing ───────────────────────────────────────────────

    async def _process_queue(self) -> None:
        try:
            while True:
                await asyncio.sleep(2)
                if not self._paused:
                    await self._drain_queue()
        except asyncio.CancelledError:
            raise

    async def _drain_queue(self) -> None:
        """Drain the admission queue one task at a time.

        Queue processing is intentionally serial — only one queued task is
        admitted per drain cycle to avoid flooding the GPU with concurrent
        loads. Uses _reserve_and_check so the queue processor doesn't race
        with concurrent submit_gpu calls.
        """
        retry: list[_QueuedGpuTask] = []
        drained = False
        while not self._queue.empty() and not drained:
            entry = self._queue.get_nowait()
            admission = await self._reserve_and_check(
                entry.task.id, entry.required_vram_mb,
            )
            if not admission.admitted:
                evicted = await self.evict_lowest_priority(
                    min_priority=int(entry.task.priority),
                )
                if evicted > 0:
                    admission = await self._reserve_and_check(
                        entry.task.id, entry.required_vram_mb,
                    )
            if admission.admitted:
                future = getattr(entry.task, "_arbiter_future", None)
                t = asyncio.create_task(
                    self._run_gpu_task(
                        entry.task, entry.required_vram_mb,
                        entry.evictable, None,
                    ),
                    name=f"gpu-arbiter-drain-{entry.task.id}",
                )
                if future is not None:
                    def _propagate(ct: asyncio.Task, f: asyncio.Future = future) -> None:
                        if f.done():
                            return
                        if ct.cancelled():
                            return
                        exc = ct.exception()
                        if exc is not None:
                            f.set_exception(exc)
                        else:
                            f.set_result(ct.result())
                    t.add_done_callback(_propagate)
                drained = True
            else:
                self._release_reservation(entry.task.id)
                retry.append(entry)

        for entry in retry:
            if not self._queue.full():
                self._queue.put_nowait(entry)
            else:
                self._dropped += 1
                future = getattr(entry.task, "_arbiter_future", None)
                if future is not None and not future.done():
                    future.set_exception(
                        NoResourceAvailableError("queue full, task dropped")
                    )

    # ── Observability ──────────────────────────────────────────────────

    async def stats(self) -> dict:
        async with self._running_lock:
            running_count = len(self._running)
        return {
            "submitted": self._submitted,
            "admitted": self._admitted,
            "queued": self._queued,
            "evicted": self._evicted,
            "dropped": self._dropped,
            "queue_depth": self._queue.qsize(),
            "running": running_count,
            "max_queue_size": self._max_queue_size,
            "eviction_enabled": self._eviction_enabled,
            "reserved_vram_mb": self._reserved_vram_mb,
            "pending_reservations": len(self._pending_reservations),
            "active_drains": len(self._draining),
        }

    async def running_tasks(self) -> list[dict]:
        async with self._running_lock:
            return [
                {
                    "task_id": tid,
                    "capability": task.capability.value,
                    "submitter": task.submitter,
                    "priority": pri,
                    "vram_mb": vram,
                    "lease_id": lid,
                }
                for tid, (task, lid, pri, vram) in self._running.items()
            ]

    def queue_snapshot(self) -> list[dict]:
        """Snapshot of queued tasks (non-destructive — re-queues after reading)."""
        items: list[_QueuedGpuTask] = []
        while not self._queue.empty():
            try:
                items.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        result = [
            {
                "task_id": e.task.id,
                "capability": e.task.capability.value,
                "priority": e.priority,
                "vram_mb": e.required_vram_mb,
                "queued_seconds": time.time() - e.queued_at,
            }
            for e in items
        ]
        for e in items:
            if not self._queue.full():
                self._queue.put_nowait(e)
        return result
