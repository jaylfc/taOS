"""API routes for browsing history."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class RecordRequest(BaseModel):
    url: str
    source_type: str
    title: str = ""
    author: str = ""
    preview: str = ""


@router.post("/api/browsing-history/record")
async def record(request: Request, body: RecordRequest):
    store = request.app.state.browsing_history
    await store.record(
        url=body.url,
        source_type=body.source_type,
        title=body.title,
        author=body.author,
        preview=body.preview,
    )
    return {"status": "ok"}


@router.get("/api/browsing-history")
async def list_history(
    request: Request,
    source_type: str | None = None,
    limit: int = 50,
):
    store = request.app.state.browsing_history
    items = await store.list_recent(source_type=source_type, limit=limit)
    return {"items": items, "count": len(items)}


@router.delete("/api/browsing-history")
async def clear_history(
    request: Request,
    source_type: str | None = None,
):
    store = request.app.state.browsing_history
    deleted = await store.clear(source_type=source_type)
    return {"deleted": deleted}
