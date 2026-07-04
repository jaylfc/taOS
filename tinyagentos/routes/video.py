# tinyagentos/routes/video.py
"""Video generation routes.

POST /api/video/generate used to block for the full multi-minute generation
(no job id, no progress, dead on any client timeout/disconnect). It now
enqueues a VideoJobStore row, runs the actual backend call in a background
task (tracked in app.state._background_tasks, same pattern as
agent_chat_router.py's dispatch()), and returns {job_id, status: "queued"}
immediately. Callers poll GET /api/video/jobs/{job_id} for status/result.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from tinyagentos.task_utils import _create_supervised_task
from tinyagentos.video_jobs import VideoJobStore

logger = logging.getLogger(__name__)

router = APIRouter()

VIDEOS_DIR_NAME = "videos"


class VideoGenerateRequest(BaseModel):
    prompt: str
    model: str = "wan2.1-1.3b"
    duration: int = 5
    resolution: str = "480x832"
    seed: int | None = None


class _VideoGenerationError(Exception):
    """Raised by _generate_and_save for known failure modes; the message is
    surfaced verbatim as the job's error field."""


def _videos_dir_from_app(app) -> Path:
    """Return the videos directory, creating it if needed.

    Takes the FastAPI ``app`` directly (rather than a Request) so the
    background generation task -- which outlives the originating request --
    can resolve the same directory.
    """
    data_dir = getattr(app.state, "data_dir", None)
    if data_dir:
        d = Path(data_dir) / VIDEOS_DIR_NAME
    else:
        d = Path(__file__).parent.parent.parent / "data" / VIDEOS_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _videos_dir(request: Request) -> Path:
    """Return the videos directory, creating it if needed."""
    return _videos_dir_from_app(request.app)


async def _get_video_job_store(app) -> VideoJobStore:
    """Get or create the VideoJobStore singleton on app.state.

    Mirrors routes/jobs.py's ``_get_queue`` -- a lazily-created, request-scoped
    singleton so no lifespan wiring is needed in app.py.
    """
    store = getattr(app.state, "video_jobs", None)
    if store is None:
        data_dir = getattr(app.state, "data_dir", None)
        base = Path(data_dir) if data_dir else Path(__file__).parent.parent.parent / "data"
        store = VideoJobStore(base / "video_jobs.db")
        await store.init()
        app.state.video_jobs = store
    return store


def _get_video_backend(request: Request) -> str | None:
    """Find a video generation backend URL.

    Priority:
    1. Explicit ``video_backend_url`` in server config.
    2. Any configured backend of type ``wangp`` (WanGP Gradio server).
    """
    config = request.app.state.config

    video_url = config.server.get("video_backend_url")
    if video_url:
        return video_url

    for backend in sorted(config.backends, key=lambda b: b.get("priority", 99)):
        if backend.get("type") == "wangp":
            return backend["url"]

    return None


def _list_videos(videos_dir: Path) -> list[dict]:
    """List generated videos with metadata, newest first."""
    results = []
    for ext in ("*.mp4", "*.webm"):
        for vid in videos_dir.glob(ext):
            meta_path = vid.with_suffix(".json")
            metadata = {}
            if meta_path.exists():
                try:
                    metadata = json.loads(meta_path.read_text())
                except (json.JSONDecodeError, OSError):
                    pass
            results.append({
                "filename": vid.name,
                "path": f"/data/videos/{vid.name}",
                "size_bytes": vid.stat().st_size,
                "prompt": metadata.get("prompt", ""),
                "model": metadata.get("model", ""),
                "duration": metadata.get("duration", 0),
                "resolution": metadata.get("resolution", ""),
                "seed": metadata.get("seed", 0),
            })
    results.sort(key=lambda x: x["filename"], reverse=True)
    return results


async def _generate_and_save(app, body: VideoGenerateRequest, seed: int, backend_url: str) -> dict:
    """Call the video backend, save the resulting clip + metadata sidecar, and
    return the same result shape the old synchronous endpoint used to return.
    Raises _VideoGenerationError on any known failure mode; the message is
    used verbatim as the job's error field."""
    # Generic OpenAI-compatible video generation endpoint
    payload = {
        "prompt": body.prompt,
        "model": body.model,
        "duration": body.duration,
        "resolution": body.resolution,
        "seed": seed,
    }

    try:
        async with httpx.AsyncClient(timeout=300) as client:
            resp = await client.post(
                f"{backend_url.rstrip('/')}/v1/videos/generations",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
    except httpx.ConnectError:
        raise _VideoGenerationError(
            f"Cannot connect to video backend at {backend_url}. Is it running?"
        )
    except httpx.TimeoutException:
        raise _VideoGenerationError(
            "Video generation timed out (>300s). The backend may be busy or overloaded."
        )
    except httpx.HTTPStatusError as e:
        raise _VideoGenerationError(
            f"Video backend returned error: {e.response.status_code}"
        )

    # Expect response with video URL or base64 data
    try:
        video_entry = data["data"][0]
        video_url = video_entry.get("url")
        video_b64 = video_entry.get("b64_mp4") or video_entry.get("b64_json")
    except (KeyError, IndexError):
        raise _VideoGenerationError("Unexpected response format from video backend")

    videos_dir = _videos_dir_from_app(app)
    timestamp = int(time.time())
    filename = f"{timestamp}_{seed}.mp4"
    video_path = videos_dir / filename

    if video_b64:
        import base64
        video_path.write_bytes(base64.b64decode(video_b64))
    elif video_url:
        # Download the video from the returned URL
        try:
            async with httpx.AsyncClient(timeout=300) as dl_client:
                dl_resp = await dl_client.get(video_url)
                dl_resp.raise_for_status()
                video_path.write_bytes(dl_resp.content)
        except Exception as e:
            raise _VideoGenerationError(f"Failed to download generated video: {str(e)}")
    else:
        raise _VideoGenerationError("Video backend returned neither url nor b64 data")

    metadata = {
        "prompt": body.prompt,
        "model": body.model,
        "duration": body.duration,
        "resolution": body.resolution,
        "seed": seed,
    }
    meta_path = videos_dir / f"{timestamp}_{seed}.json"
    meta_path.write_text(json.dumps(metadata, indent=2))

    return {
        "filename": filename,
        "path": f"/data/videos/{filename}",
        **metadata,
    }


async def _record_job_error(store, job_id: str, message: str) -> None:
    """Best-effort: mark a job errored. If the update itself fails (e.g. the
    store's db is gone at shutdown), log it -- but never raise, so the caller's
    terminal-state guarantee holds even when the DB write can't."""
    try:
        await store.update_job(job_id, status="error", error=message, completed_at=time.time())
    except Exception:
        logger.exception("video job %s: failed to record error status", job_id)


async def _run_generation_job(app, job_id: str, body: VideoGenerateRequest, seed: int, backend_url: str) -> None:
    """Background task: run the generation and record the outcome on the job.
    Never raises, and always leaves the job in a terminal state (done/error)
    -- never stuck in "running" -- so a poller always sees a final status."""
    store = await _get_video_job_store(app)
    await store.update_job(job_id, status="running")
    try:
        result = await _generate_and_save(app, body, seed, backend_url)
    except _VideoGenerationError as exc:
        await _record_job_error(store, job_id, str(exc))
        return
    except Exception as exc:
        logger.exception("video generation job %s crashed", job_id)
        await _record_job_error(store, job_id, f"Unexpected error: {exc}")
        return
    try:
        await store.update_job(
            job_id, status="done", progress=1.0, result_json=json.dumps(result), completed_at=time.time()
        )
    except Exception:
        # The video is saved to the library regardless; only the job row failed
        # to flip to done. Record an error so the poller doesn't hang on
        # "running" forever, and the user can still find the clip in Library.
        logger.exception("video job %s: failed to record done status", job_id)
        await _record_job_error(store, job_id, "Generation finished but the job status could not be saved. Check the Library.")


@router.post("/api/video/generate")
async def generate_video(request: Request, body: VideoGenerateRequest):
    """Enqueue a video generation job and return its id immediately.

    Fails fast with the existing 503 when no backend is configured at all --
    there's nothing to enqueue in that case. Once a backend is configured,
    the actual generation (and any backend-side failure) happens in a
    background task; poll GET /api/video/jobs/{job_id} for the outcome.
    """
    backend_url = _get_video_backend(request)
    if not backend_url:
        return JSONResponse(
            {"error": "No video generation backend configured. Connect a GPU worker or set video_backend_url in config."},
            status_code=503,
        )

    seed = body.seed if body.seed is not None else random.randint(0, 2**32 - 1)

    store = await _get_video_job_store(request.app)
    job_id = await store.create_job()

    task_set = getattr(request.app.state, "_background_tasks", None)
    coro = _run_generation_job(request.app, job_id, body, seed, backend_url)
    if task_set is None:
        asyncio.create_task(coro)
    else:
        _create_supervised_task(coro, task_set)

    return JSONResponse({"job_id": job_id, "status": "queued"}, status_code=202)


@router.get("/api/video/jobs/{job_id}")
async def get_video_job(request: Request, job_id: str):
    """Poll a video generation job's status.

    Returns ``{status: queued|running|done|error, progress?, result?, error?}``.
    ``result`` (present when done) is the same shape the old synchronous
    endpoint used to return -- the video is already saved to the library by
    the time status flips to "done".
    """
    store = await _get_video_job_store(request.app)
    job = await store.get_job(job_id)
    if not job:
        return JSONResponse({"error": f"Job '{job_id}' not found"}, status_code=404)

    response: dict = {"job_id": job["id"], "status": job["status"]}
    if job.get("progress") is not None:
        response["progress"] = job["progress"]
    if job["status"] == "done" and job.get("result_json"):
        try:
            response["result"] = json.loads(job["result_json"])
        except (json.JSONDecodeError, TypeError):
            # A corrupt result blob shouldn't 500 the poller. Downgrade to an
            # error status with a clear message -- the clip is still in Library.
            logger.warning("video job %s: result_json is not valid JSON", job_id)
            response["status"] = "error"
            response["error"] = "Job result is corrupt. Check the Library for the video."
    if job["status"] == "error" and job.get("error"):
        response["error"] = job["error"]
    return response


@router.get("/api/video")
async def list_videos(request: Request):
    """List all generated videos with metadata, newest first."""
    videos_dir = _videos_dir(request)
    return {"videos": _list_videos(videos_dir)}


@router.get("/api/video/{filename}")
async def serve_video(request: Request, filename: str):
    """Serve a generated video file for playback/download.

    The ``path`` field returned by generate/list is not itself a mounted
    static route -- the frontend hits this endpoint by filename instead.
    """
    videos_dir = _videos_dir(request)

    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)

    video_path = videos_dir / filename
    if not video_path.exists():
        return JSONResponse({"error": f"Video '{filename}' not found"}, status_code=404)

    return FileResponse(video_path, media_type="video/mp4", filename=filename)


@router.delete("/api/video/{filename}")
async def delete_video(request: Request, filename: str):
    """Delete a generated video and its metadata sidecar."""
    videos_dir = _videos_dir(request)

    if "/" in filename or "\\" in filename or ".." in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)

    video_path = videos_dir / filename
    if not video_path.exists():
        return JSONResponse({"error": f"Video '{filename}' not found"}, status_code=404)

    video_path.unlink()
    meta_path = video_path.with_suffix(".json")
    if meta_path.exists():
        meta_path.unlink()

    return {"status": "deleted", "filename": filename}
