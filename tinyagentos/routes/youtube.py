from __future__ import annotations

"""API routes for YouTube knowledge ingestion.

All routes live under /api/youtube/. The router reads state from
``request.app.state``:

- ``ingest_pipeline``  — IngestPipeline instance
- ``knowledge_store``  — KnowledgeStore instance
"""

import asyncio
import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.knowledge_fetchers.youtube import download_video

logger = logging.getLogger(__name__)

router = APIRouter()

# In-memory download status tracker: item_id -> "idle"|"downloading"|"complete"|"error"
_download_status: dict[str, str] = {}


# ------------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------------

class YoutubeIngestRequest(BaseModel):
    url: str
    title: str = ""


class YoutubeDownloadRequest(BaseModel):
    item_id: str
    quality: str = "720"


# ------------------------------------------------------------------
# POST /api/youtube/ingest
# ------------------------------------------------------------------

@router.post("/api/youtube/ingest")
async def youtube_ingest(request: Request, body: YoutubeIngestRequest):
    """Submit a YouTube URL for ingestion via the IngestPipeline.

    Returns immediately with the new item id and status='pending'.
    """
    pipeline = request.app.state.ingest_pipeline
    try:
        item_id = await pipeline.submit_background(
            url=body.url,
            title=body.title,
            source="youtube",
        )
        return {"id": item_id, "status": "pending"}
    except Exception as exc:
        logger.exception("youtube ingest failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


# ------------------------------------------------------------------
# POST /api/youtube/download
# ------------------------------------------------------------------

@router.post("/api/youtube/download")
async def youtube_download(request: Request, body: YoutubeDownloadRequest):
    """Fire a background video download for a previously ingested item.

    Returns immediately with status='downloading'.
    """
    store = request.app.state.knowledge_store
    item = await store.get_item(body.item_id)
    if item is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    source_url = item.get("source_url", "")
    if not source_url:
        return JSONResponse({"error": "item has no source URL"}, status_code=400)

    _download_status[body.item_id] = "downloading"

    async def _bg_download():
        try:
            path = await download_video(source_url, quality=body.quality)
            if path:
                _download_status[body.item_id] = "complete"
                # Persist media_path on the item if the store supports it
                try:
                    await store.update_item(body.item_id, media_path=path)
                except Exception:
                    pass  # best-effort
            else:
                _download_status[body.item_id] = "error"
        except Exception as exc:
            logger.exception("Background video download failed for %s: %s", body.item_id, exc)
            _download_status[body.item_id] = "error"

    asyncio.create_task(_bg_download())
    return {"status": "downloading", "item_id": body.item_id}


# ------------------------------------------------------------------
# GET /api/youtube/download-status/{item_id}
# ------------------------------------------------------------------

@router.get("/api/youtube/download-status/{item_id}")
async def youtube_download_status(request: Request, item_id: str):
    """Return the download status for a YouTube item.

    Possible statuses: idle, downloading, complete, error.
    Also returns file_size and path if the item has a media_path.
    """
    store = request.app.state.knowledge_store
    item = await store.get_item(item_id)
    if item is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    status = _download_status.get(item_id, "idle")

    # Check if a media file already exists on the item
    media_path = item.get("media_path")
    if media_path and Path(media_path).exists():
        file_size = Path(media_path).stat().st_size
        return {"status": "complete", "path": media_path, "file_size": file_size}

    return {"status": status, "item_id": item_id}


# ------------------------------------------------------------------
# GET /api/youtube/transcript/{item_id}
# ------------------------------------------------------------------

@router.get("/api/youtube/transcript/{item_id}")
async def youtube_transcript(request: Request, item_id: str):
    """Return transcript segments and chapters for a YouTube item.

    Reads from KnowledgeItem metadata.transcript_segments and metadata.chapters.
    """
    store = request.app.state.knowledge_store
    item = await store.get_item(item_id)
    if item is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    metadata = item.get("metadata") or {}
    segments = metadata.get("transcript_segments") or []
    chapters = metadata.get("chapters") or []

    return {"segments": segments, "chapters": chapters}
