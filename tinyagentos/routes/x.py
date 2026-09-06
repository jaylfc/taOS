from __future__ import annotations

"""API routes for X (Twitter) tweet viewer and author watch management.

All routes live under /api/x/. The router reads these from
``request.app.state``:

- ``http_client``  -- shared httpx.AsyncClient (for future cookie-auth calls)

The XWatchStore is instantiated lazily from app state or a module-level
singleton, depending on how the main app wires it up.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.knowledge_fetchers.x import (
    XWatchStore,
    fetch_tweet_ytdlp,
    reconstruct_thread,
    stitch_thread_text,
    extract_metadata,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# Module-level watch store used when not overridden by app.state
_watch_store: XWatchStore | None = None


def _get_watch_store(request: Request) -> XWatchStore:
    """Return XWatchStore from app state or fall back to module singleton."""
    global _watch_store
    store: XWatchStore | None = getattr(request.app.state, "x_watch_store", None)
    if store is not None:
        return store
    if _watch_store is None:
        _watch_store = XWatchStore()
        _watch_store.init()
    return _watch_store


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CreateWatchRequest(BaseModel):
    handle: str
    filters: dict | None = None
    frequency: int = 1800


class UpdateWatchRequest(BaseModel):
    filters: dict | None = None
    frequency: int | None = None
    enabled: bool | None = None


# ---------------------------------------------------------------------------
# Tweet endpoints
# ---------------------------------------------------------------------------

@router.get("/api/x/tweet/{tweet_id}")
async def get_tweet(request: Request, tweet_id: str):
    """Fetch a single tweet via yt-dlp.

    Path params:
        tweet_id  -- numeric tweet / status ID

    Returns the normalised tweet dict or 404 if yt-dlp cannot fetch it.
    """
    url = f"https://twitter.com/i/web/status/{tweet_id}"
    try:
        tweet = await fetch_tweet_ytdlp(url)
    except Exception as exc:
        logger.exception("fetch_tweet_ytdlp failed for %s: %s", tweet_id, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)

    if tweet is None:
        return JSONResponse({"error": "Could not fetch tweet"}, status_code=404)

    return {
        **tweet,
        "metadata": extract_metadata(tweet),
    }


@router.get("/api/x/thread/{tweet_id}")
async def get_thread(request: Request, tweet_id: str):
    """Reconstruct a tweet thread.

    v1: returns the single tweet via yt-dlp.  Future versions will walk the
    reply chain using cookie-authenticated GraphQL calls.

    Path params:
        tweet_id  -- numeric tweet / status ID

    Returns:
        {tweets: list[dict], text: str}
    """
    http_client = getattr(request.app.state, "http_client", None)
    try:
        tweets = await reconstruct_thread(tweet_id, cookies=None, http_client=http_client)
    except Exception as exc:
        logger.exception("reconstruct_thread failed for %s: %s", tweet_id, exc)
        return JSONResponse({"error": str(exc)}, status_code=502)

    return {
        "tweets": tweets,
        "text": stitch_thread_text(tweets),
    }


# ---------------------------------------------------------------------------
# Auth status endpoint
# ---------------------------------------------------------------------------

@router.get("/api/x/auth/status")
async def auth_status(request: Request):
    """Return X authentication status.

    v1 always returns unauthenticated.  Cookie integration via the Agent
    Browsers app is planned for a future release.

    Returns:
        {authenticated: bool}
    """
    return {"authenticated": False}


# ---------------------------------------------------------------------------
# Watch CRUD endpoints
# ---------------------------------------------------------------------------

@router.post("/api/x/watch")
async def create_watch(request: Request, body: CreateWatchRequest):
    """Create a new author watch.

    Body:
        handle    -- X handle (with or without leading @)
        filters   -- optional filter dict
        frequency -- check interval in seconds (default 1800)

    Returns 409 if a watch for this handle already exists.
    """
    store = _get_watch_store(request)
    try:
        watch = store.create_watch(
            handle=body.handle,
            filters=body.filters,
            frequency=body.frequency,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except Exception as exc:
        logger.exception("create_watch failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
    return watch


@router.get("/api/x/watches")
async def list_watches(request: Request):
    """Return all author watches.

    Returns:
        {watches: list[dict]}
    """
    store = _get_watch_store(request)
    try:
        watches = store.list_watches()
    except Exception as exc:
        logger.exception("list_watches failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)
    return {"watches": watches}


@router.put("/api/x/watch/{handle}")
async def update_watch(request: Request, handle: str, body: UpdateWatchRequest):
    """Update an existing author watch.

    Path params:
        handle  -- X handle (with or without leading @)

    Body (all optional):
        filters   -- new filter dict
        frequency -- new check interval in seconds
        enabled   -- true/false

    Returns 404 if the handle is not found.
    """
    store = _get_watch_store(request)
    updates: dict = {}
    if body.filters is not None:
        updates["filters"] = body.filters
    if body.frequency is not None:
        updates["frequency"] = body.frequency
    if body.enabled is not None:
        updates["enabled"] = int(body.enabled)

    try:
        watch = store.update_watch(handle, updates)
    except Exception as exc:
        logger.exception("update_watch failed for %s: %s", handle, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    if watch is None:
        return JSONResponse({"error": f"Watch for @{handle} not found"}, status_code=404)
    return watch


@router.delete("/api/x/watch/{handle}")
async def delete_watch(request: Request, handle: str):
    """Delete an author watch.

    Path params:
        handle  -- X handle (with or without leading @)

    Returns 404 if the handle is not found.
    """
    store = _get_watch_store(request)
    try:
        deleted = store.delete_watch(handle)
    except Exception as exc:
        logger.exception("delete_watch failed for %s: %s", handle, exc)
        return JSONResponse({"error": str(exc)}, status_code=500)

    if not deleted:
        return JSONResponse({"error": f"Watch for @{handle} not found"}, status_code=404)
    return {"deleted": True, "handle": handle.lstrip("@")}
