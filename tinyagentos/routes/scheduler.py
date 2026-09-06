"""Scheduler observability — live stats and task history for the Activity app."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/api/scheduler/stats")
async def scheduler_stats(request: Request):
    """Live scheduler state: resources, concurrency, active tasks, counters."""
    scheduler = getattr(request.app.state, "resource_scheduler", None)
    if scheduler is None:
        return JSONResponse(
            {"error": "resource scheduler not initialised"}, status_code=503
        )
    return scheduler.stats()


@router.get("/api/scheduler/tasks")
async def scheduler_tasks(request: Request, limit: int = 100):
    """Recent task history, newest first. Used by the Activity app widget."""
    scheduler = getattr(request.app.state, "resource_scheduler", None)
    if scheduler is None:
        return JSONResponse(
            {"error": "resource scheduler not initialised"}, status_code=503
        )
    limit = max(1, min(500, int(limit)))
    return {"tasks": [r.to_dict() for r in scheduler.history(limit=limit)]}


@router.get("/api/scheduler/backends")
async def scheduler_backends(request: Request):
    """Live backend catalog view — what's loaded where right now.

    This is the backend-driven discovery view that subsystems query for
    "is X available?" questions.
    """
    catalog = getattr(request.app.state, "backend_catalog", None)
    if catalog is None:
        return JSONResponse({"error": "backend catalog not initialised"}, status_code=503)
    return {
        "backends": [b.to_dict() for b in catalog.backends()],
        "models": catalog.all_models(),
    }
