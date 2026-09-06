"""Benchmark controller API.

Workers call these endpoints to:
- Post results from their on-join benchmark run (first_join=True)
- Post results from manual reruns (first_join=False)

The UI and scheduler cost model read:
- GET /api/workers/{id}/benchmark — per-worker history
- GET /api/benchmarks/capability/{cap} — cross-worker leaderboard
- POST /api/workers/{id}/benchmark — trigger a manual run (Phase 2)
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter()


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
    results: list[BenchmarkResult]


def _store(request: Request):
    return getattr(request.app.state, "benchmark_store", None)


@router.post("/api/workers/{worker_id}/benchmark/results")
async def post_benchmark_results(worker_id: str, report: BenchmarkReport, request: Request):
    """Worker posts benchmark results here.

    Enforces the 'first_join runs exactly once' invariant — if the worker
    has already posted a first_join=True record, subsequent first_join
    posts are coerced to first_join=False (i.e. treated as manual reruns)
    so the history stays clean.
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

    return {
        "worker_id": worker_id,
        "recorded": recorded,
        "first_join": first_join_allowed,
    }


@router.get("/api/workers/{worker_id}/benchmark")
async def get_worker_benchmarks(worker_id: str, request: Request, limit: int = 100):
    """Per-worker benchmark history, newest first."""
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
