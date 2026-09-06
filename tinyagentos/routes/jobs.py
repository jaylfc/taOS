"""Job Queue API routes — view and manage scheduled memory pipeline jobs."""

from __future__ import annotations

import logging
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter(tags=["jobs"])


async def _get_queue(request: Request):
    """Get or create job queue from app state."""
    queue = getattr(request.app.state, "job_queue", None)
    if queue is None:
        from pathlib import Path
        from tinyagentos.scheduling.job_queue import JobQueue
        data_dir = getattr(request.app.state, "data_dir", Path("data"))
        queue = JobQueue(db_path=data_dir / "job-queue.db")
        await queue.init()
        request.app.state.job_queue = queue
    return queue


@router.get("/api/jobs")
async def list_jobs(request: Request, status: str | None = None, limit: int = 50):
    """List recent jobs, optionally filtered by status."""
    queue = await _get_queue(request)
    return await queue.recent(limit=limit, status=status)


@router.get("/api/jobs/stats")
async def job_stats(request: Request):
    """Job queue statistics."""
    queue = await _get_queue(request)
    return await queue.stats()


@router.get("/api/jobs/running")
async def running_jobs(request: Request):
    """Currently running jobs."""
    queue = await _get_queue(request)
    return await queue.running_jobs()


@router.post("/api/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, request: Request):
    """Cancel a pending job."""
    queue = await _get_queue(request)
    cancelled = await queue.cancel(job_id)
    if cancelled:
        return {"status": "cancelled", "job_id": job_id}
    return JSONResponse({"error": "Job not found or not pending"}, status_code=404)


@router.get("/api/jobs/{job_id}")
async def get_job(job_id: str, request: Request):
    """Get details of a specific job."""
    queue = await _get_queue(request)
    job = await queue.get_job(job_id)
    if not job:
        return JSONResponse({"error": "Job not found"}, status_code=404)
    return job


@router.post("/api/jobs/cleanup")
async def cleanup_jobs(request: Request):
    """Remove completed/failed jobs older than 7 days."""
    queue = await _get_queue(request)
    removed = await queue.cleanup()
    return {"removed": removed}
