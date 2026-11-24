"""Scheduler -- dispatcher across registered Resources.

Phase 1 is deliberately minimal: tasks are routed synchronously in the
order of their preferred_resources list, the first resource that passes
admission runs it, and every dispatch is recorded to a bounded history
for the Activity app.

Priority-queue sharing across resources, task aging, mid-task preemption,
and max_wait_ms enforcement are Phase 2 work and intentionally absent
here -- adding them before the single-resource path is proven would mask
the simpler bugs.

Phase 1.5 core-aware extension
-------------------------------
#62 / tsk-mo6f5o: CoreAwareModelScheduler (tinyagentos/scheduler/
core_aware_scheduler.py) is now wired into the gateway forward path.
The scheduler instance is constructed in tinyagentos/app.py as
app.state.model_scheduler with the Model Activity feed. The gateway
calls begin_use(model, backend) before each request and end_use(model)
in a finally block after (success, error, timeout, stream end, client
abort). load_with_core_awareness() is used by begin_use() for
first-time registration of models unknown to the registry (the fast
path _make_no_core_model builds a record without side effects, then
register_loaded() adds it). For NPU backends (rkllama) the backend
loads the model before the request reaches the gateway; begin_use()
registers the model with the cores the backend reports, without
contention resolution.
"""
from __future__ import annotations

import asyncio
import collections
import logging
import time
from typing import Optional, TYPE_CHECKING

from tinyagentos.scheduler.resource import Resource
from tinyagentos.scheduler.types import (
    NoResourceAvailableError,
    Task,
    TaskRecord,
    TaskStatus,
)

if TYPE_CHECKING:
    from tinyagentos.scheduler.history_store import HistoryStore

logger = logging.getLogger(__name__)

HISTORY_MAX = 500


class Scheduler:
    """Registers Resources and dispatches Tasks.

    Thread/loop model: in-process with the main FastAPI app, asyncio-native.
    Task submission is async and returns when the task has actually run
    (or raised). This makes the caller's code read linearly:

        result = await scheduler.submit(task)
    """

    def __init__(self, history_store: "HistoryStore | None" = None):
        self._resources: dict[str, Resource] = {}
        self._history: collections.deque[TaskRecord] = collections.deque(maxlen=HISTORY_MAX)
        self._history_store = history_store
        self._lock = asyncio.Lock()
        self._submitted = 0
        self._rejected = 0
        self._errors = 0
        self._completed = 0

    def _persist_terminal(self, record: TaskRecord) -> None:
        """Fire-and-forget write to the persistent history store.

        Called from the submit path on terminal transitions. If no store
        is wired up (tests, startup race) this is a no-op. Failures are
        logged by the store itself and never propagate to the caller.
        """
        if self._history_store is None:
            return
        try:
            asyncio.create_task(
                self._history_store.record_terminal(record),
                name=f"scheduler-history-{record.task_id}",
            )
        except RuntimeError:
            # No running loop (e.g. being called from a sync test)
            pass

    def register(self, resource: Resource) -> None:
        """Add a resource. Safe to call during startup only."""
        if resource.name in self._resources:
            logger.warning("Re-registering resource %s", resource.name)
        self._resources[resource.name] = resource
        logger.info(
            "scheduler: registered resource %s (%s/%s/%s, concurrency=%d)",
            resource.name,
            resource.signature.platform,
            resource.signature.runtime,
            resource.signature.runtime_version or "any",
            resource.concurrency,
        )

    def resources(self) -> list[Resource]:
        return list(self._resources.values())

    def get_resource(self, name: str) -> Optional[Resource]:
        return self._resources.get(name)

    async def submit(self, task: Task) -> object:
        """Dispatch a task to the first admitted preferred resource.

        If ``task.preferred_resources`` is empty, the scheduler auto-routes
        by ``(lowest tier, best benchmark score)``, GPU before NPU before
        CPU, with benchmark scores breaking ties within a tier once the
        benchmark store has data for the (resource, capability) pair.

        Raises :class:`NoResourceAvailableError` if no resource can run it.
        """
        self._submitted += 1
        record = TaskRecord(
            task_id=task.id,
            capability=task.capability.value,
            submitter=task.submitter,
            priority=int(task.priority),
            resource=None,
            status=TaskStatus.QUEUED,
            submitted_at=task.submitted_at,
        )
        self._history.append(record)

        # Build the candidate order. If the caller supplied explicit
        # preferred_resources we honour that order. Otherwise we
        # auto-route by (tier, benchmark score).
        if task.preferred_resources:
            candidates = [
                self._resources.get(ref.name) for ref in task.preferred_resources
            ]
            # Keep explicit ref order but filter out unknowns, which we
            # still record in `tried` so observability is honest.
            ordered: list = []
            tried: list[str] = []
            for ref, resource in zip(task.preferred_resources, candidates):
                if resource is None:
                    tried.append(f"{ref.name}=unknown")
                    continue
                ordered.append((resource, ref.name))
        else:
            # Auto-route: every resource that might admit the task, sorted
            # by (tier, -score). Lower tier wins; higher score wins ties.
            ordered_with_key: list[tuple[int, float, "Resource", str]] = []
            tried = []
            for resource in self._resources.values():
                if task.capability.value not in resource.capabilities:
                    # Not ready right now (backend-driven), admission would
                    # reject. Include in the tried list so the error is clear.
                    tried.append(
                        f"{resource.name}=capability '{task.capability.value}' not loaded"
                    )
                    continue
                score = resource.score_for(task.capability.value, None) or 0.0
                ordered_with_key.append(
                    (resource.tier, -score, resource, resource.name)
                )
            ordered_with_key.sort(key=lambda x: (x[0], x[1]))
            ordered = [(r, name) for _, _, r, name in ordered_with_key]

        for resource, ref_name in ordered:
            admitted, reason = resource.can_admit(task)
            if not admitted:
                tried.append(f"{ref_name}={reason}")
                continue

            # Found a home, run it
            record.resource = resource.name
            record.status = TaskStatus.RUNNING
            record.started_at = time.time()
            try:
                result, elapsed = await resource.run(task)
                record.status = TaskStatus.COMPLETE
                record.completed_at = time.time()
                record.elapsed_seconds = elapsed
                self._completed += 1
                self._persist_terminal(record)
                return result
            except Exception as exc:  # noqa: BLE001, propagate after recording
                record.status = TaskStatus.ERROR
                record.completed_at = time.time()
                record.error = str(exc)
                self._errors += 1
                logger.exception(
                    "scheduler: task %s on %s raised", task.id, resource.name
                )
                self._persist_terminal(record)
                raise

        # No resource accepted
        record.status = TaskStatus.REJECTED
        record.completed_at = time.time()
        record.error = "; ".join(tried) or "no preferred resources"
        self._rejected += 1
        self._persist_terminal(record)
        raise NoResourceAvailableError(
            f"no resource can run {task.capability.value} "
            f"(submitter={task.submitter}, tried: {record.error})"
        )

    def history(self, limit: int = 100) -> list[TaskRecord]:
        """Most recent in-memory task records, newest first.

        Bounded by HISTORY_MAX, for older records use
        :meth:`history_since` which reads from the persistent store.
        """
        return list(self._history)[-limit:][::-1]

    async def history_since(self, timestamp: float, limit: int = 500) -> list[dict]:
        """Persistent history query, returns dicts, newest first.

        Reads from the :class:`HistoryStore` if one is wired up, empty
        list otherwise. Use ``history()`` for the in-memory hot path
        (running tasks, very recent completions) and ``history_since``
        for trend windows that exceed the deque cap.
        """
        if self._history_store is None:
            return []
        return await self._history_store.since(timestamp, limit=limit)

    def stats(self) -> dict:
        active = sum(r.in_flight for r in self._resources.values())
        return {
            "submitted": self._submitted,
            "completed": self._completed,
            "errors": self._errors,
            "rejected": self._rejected,
            "active": active,
            "resources": [
                {
                    "name": r.name,
                    "platform": r.signature.platform,
                    "runtime": r.signature.runtime,
                    "runtime_version": r.signature.runtime_version,
                    "concurrency": r.concurrency,
                    "in_flight": r.in_flight,
                    "tier": r.tier,
                    "capabilities": sorted(r.capabilities),
                    "potential_capabilities": sorted(r.potential_capabilities),
                }
                for r in self._resources.values()
            ],
        }
