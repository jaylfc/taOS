"""Benchmark controller API.

Workers call these endpoints to:
- Post results from their on-join benchmark run (first_join=True)
- Post results from manual reruns (first_join=False)

The UI and scheduler cost model read:
- GET /api/workers/{id}/benchmark — per-worker history + queued run
- GET /api/benchmarks/capability/{cap} — cross-worker leaderboard

The UI writes one thing:
- POST /api/workers/{id}/benchmark — queue a manual run

Queueing rather than pushing is deliberate: the worker agent is a poller
(register + heartbeat) with no inbound HTTP surface, so the queued run is
handed to the worker in its next heartbeat response
(``tinyagentos/routes/cluster.py``) and the worker starts
``python -m tinyagentos.benchmark.runner``. Results come back through the
results endpoint below, which also clears the queue entry.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tinyagentos.auth_context import require_admin

logger = logging.getLogger(__name__)

router = APIRouter()

# Queueing a run occupies a worker, so it is an admin action (same gate as
# the worker update/deploy endpoints in routes/cluster.py).
_ADMIN = [Depends(require_admin)]


class BenchmarkResult(BaseModel):
    task_id: str
    capability: str
    model: str
    metric: str
    value: float | None = None
    unit: str = ""
    status: str
    elapsed_seconds: float = 0.0
    error: str | None = None
    measured_at: float = Field(default_factory=time.time)
    details: dict[str, Any] = Field(default_factory=dict)


class BenchmarkReport(BaseModel):
    worker_id: str
    worker_name: str | None = None
    platform: str | None = None
    suite_name: str | None = None
    first_join: bool = False
    # ``requested_at`` of the queued manual run this report is serving, echoed
    # back by the runner (``--request-id``). It is what lets the controller
    # clear exactly the run that produced these rows: a click that arrived
    # while this run was in flight must survive, not be swept away by results
    # that predate it. Absent for the first-attach run, which serves no queue
    # entry.
    request_id: float | None = None
    results: list[BenchmarkResult]


class BenchmarkTrigger(BaseModel):
    """Body for a manual run. ``force`` replaces an already-queued run."""

    force: bool = False


def _store(request: Request):
    return getattr(request.app.state, "benchmark_store", None)


@router.post("/api/workers/{worker_id}/benchmark/results")
async def post_benchmark_results(worker_id: str, report: BenchmarkReport, request: Request):
    """Worker posts benchmark results here.

    Enforces the 'first_join runs exactly once' invariant — if the worker
    has already posted a first_join=True record, subsequent first_join
    posts are coerced to first_join=False (i.e. treated as manual reruns)
    so the history stays clean.

    Recording is append-only: every post adds rows, so a re-run never
    overwrites the first-join baseline or an earlier run. Once results have
    landed, any queued manual run for this worker is cleared — that is how
    the worker tells the controller its click was served.
    """
    store = _store(request)
    if store is None:
        return JSONResponse(
            {"error": "benchmark store not initialised"}, status_code=503
        )

    first_join_allowed = report.first_join
    if first_join_allowed and await store.has_first_join_run(worker_id):
        logger.info(
            "worker %s tried to post first_join=True but already has one; coercing to manual",
            worker_id,
        )
        first_join_allowed = False

    recorded = 0
    for result in report.results:
        try:
            await store.record(
                worker_id=worker_id,
                worker_name=report.worker_name,
                platform=report.platform,
                capability=result.capability,
                model=result.model,
                metric=result.metric,
                value=result.value,
                unit=result.unit,
                status=result.status,
                elapsed_seconds=result.elapsed_seconds,
                error=result.error,
                details=result.details,
                suite_name=report.suite_name,
                first_join=first_join_allowed,
                measured_at=result.measured_at,
            )
            recorded += 1
        except Exception:
            logger.exception("failed to record benchmark result")

    # Only a run that actually recorded something, and that names the queued
    # run it was serving, may clear the queue entry. The id match happens inside
    # the DELETE, so a run queued while this report was in flight cannot be
    # removed by it (read-then-delete would have that race): the in-flight
    # report carries the OLDER id and matches nothing.
    if recorded and report.request_id is not None:
        try:
            await store.clear_pending_request(worker_id, requested_at=report.request_id)
        except Exception:
            logger.exception("failed to clear queued benchmark run for %s", worker_id)

    return {
        "worker_id": worker_id,
        "recorded": recorded,
        "first_join": first_join_allowed,
    }


@router.post("/api/workers/{worker_id}/benchmark", dependencies=_ADMIN)
async def trigger_worker_benchmark(
    worker_id: str,
    request: Request,
    body: BenchmarkTrigger | None = None,
):
    """Queue a manual benchmark run on one worker.

    Returns 202 with the queued request; the worker starts the suite on its
    next heartbeat (≈5s) and posts results to the results endpoint, which
    clears the queue entry. Nothing re-runs automatically afterwards.

    - 404 when the controller does not know the worker
    - 409 when the worker is not online, or when a run is already queued
      (pass ``{"force": true}`` to replace the queued run)
    """
    store = _store(request)
    if store is None:
        return JSONResponse(
            {"error": "benchmark store not initialised"}, status_code=503
        )

    cluster = getattr(request.app.state, "cluster_manager", None)
    if cluster is None:
        return JSONResponse(
            {"error": "cluster manager not initialised"}, status_code=503
        )
    worker = cluster.get_worker(worker_id)
    if worker is None:
        return JSONResponse(
            {"error": f"Worker '{worker_id}' not found"}, status_code=404
        )

    force = bool(body.force) if body is not None else False

    # Offline is the more actionable answer, so it wins: telling a user "already
    # queued" about a worker that cannot run anything would read as "busy".
    status = getattr(worker, "status", None)
    if status != "online":
        return JSONResponse(
            {
                "error": (
                    f"Worker '{worker_id}' is not online (status={status}) — "
                    "a benchmark needs the worker up"
                )
            },
            status_code=409,
        )

    # The "already queued" guard lives inside the write (one conditional
    # upsert), so two concurrent clicks cannot both see an empty queue and
    # both think they queued the run. None means the write was refused.
    queued = await store.request_run(worker_id=worker_id, force=force)
    if queued is None:
        return JSONResponse(
            {
                "error": f"A benchmark run is already queued for worker '{worker_id}'",
                "pending": await store.get_pending_request(worker_id),
                "hint": 'POST {"force": true} to replace the queued run.',
            },
            status_code=409,
        )

    logger.info(
        "queued manual benchmark run for worker %s (force=%s)", worker_id, force
    )
    return JSONResponse(
        {
            "status": "queued",
            **queued,
            "worker_name": getattr(worker, "name", worker_id),
            "message": (
                "Queued. The worker starts the suite on its next heartbeat and "
                "posts results back to the controller."
            ),
        },
        status_code=202,
    )


@router.get("/api/workers/{worker_id}/benchmark")
async def get_worker_benchmarks(worker_id: str, request: Request, limit: int = 100):
    """Per-worker benchmark history, newest first.

    ``latest`` is the newest row per (capability, model) — what the worker
    scores today; ``history`` is every recorded run; ``pending`` is the
    queued manual run, if one is waiting for the worker's next heartbeat.
    """
    store = _store(request)
    if store is None:
        return JSONResponse(
            {"error": "benchmark store not initialised"}, status_code=503
        )
    latest = await store.latest_by_worker(worker_id)
    history = await store.history_by_worker(worker_id, limit=limit)
    return {
        "worker_id": worker_id,
        "latest": latest,
        "history": history,
        "pending": await store.get_pending_request(worker_id),
    }


@router.get("/api/benchmarks/capability/{capability}")
async def get_capability_leaderboard(capability: str, request: Request, metric: str | None = None):
    """Cross-worker leaderboard for a capability.

    Used by the cluster dispatcher's cost model to pick the best worker
    for a given capability, and by the Cluster page UI to show who's
    fastest at what.
    """
    store = _store(request)
    if store is None:
        return JSONResponse(
            {"error": "benchmark store not initialised"}, status_code=503
        )
    entries = await store.leaderboard(capability=capability, metric=metric)
    return {
        "capability": capability,
        "metric": metric,
        "entries": entries,
    }
