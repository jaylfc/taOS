# tinyagentos/routes/video.py
from __future__ import annotations

import json
import random
import time
from pathlib import Path

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

router = APIRouter()

VIDEOS_DIR_NAME = "videos"


class VideoGenerateRequest(BaseModel):
    prompt: str
    model: str = "wan2.1-1.3b"
    duration: int = 5
    resolution: str = "480x832"
    seed: int | None = None


def _videos_dir(request: Request) -> Path:
    """Return the videos directory, creating it if needed."""
    data_dir = getattr(request.app.state, "data_dir", None)
    if data_dir:
        d = Path(data_dir) / VIDEOS_DIR_NAME
    else:
        d = Path(__file__).parent.parent.parent / "data" / VIDEOS_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


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


@router.post("/api/video/generate")
async def generate_video(request: Request, body: VideoGenerateRequest):
    """Generate a video from a text prompt using any configured video backend."""
    backend_url = _get_video_backend(request)
    if not backend_url:
        return JSONResponse(
            {"error": "No video generation backend configured. Connect a GPU worker or set video_backend_url in config."},
            status_code=503,
        )

    seed = body.seed if body.seed is not None else random.randint(0, 2**32 - 1)

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
        return JSONResponse(
            {"error": f"Cannot connect to video backend at {backend_url}. Is it running?"},
            status_code=503,
        )
    except httpx.TimeoutException:
        return JSONResponse(
            {"error": "Video generation timed out (>300s). The backend may be busy or overloaded."},
            status_code=504,
        )
    except httpx.HTTPStatusError as e:
        return JSONResponse(
            {"error": f"Video backend returned error: {e.response.status_code}"},
            status_code=502,
        )
    except Exception as e:
        return JSONResponse(
            {"error": f"Unexpected error: {str(e)}"},
            status_code=500,
        )

    # Expect response with video URL or base64 data
    try:
        video_entry = data["data"][0]
        video_url = video_entry.get("url")
        video_b64 = video_entry.get("b64_mp4") or video_entry.get("b64_json")
    except (KeyError, IndexError):
        return JSONResponse(
            {"error": "Unexpected response format from video backend"},
            status_code=502,
        )

    videos_dir = _videos_dir(request)
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
            return JSONResponse(
                {"error": f"Failed to download generated video: {str(e)}"},
                status_code=502,
            )
    else:
        return JSONResponse(
            {"error": "Video backend returned neither url nor b64 data"},
            status_code=502,
        )

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
        "status": "generated",
        "filename": filename,
        "path": f"/data/videos/{filename}",
        **metadata,
    }


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
