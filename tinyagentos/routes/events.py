from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from tinyagentos.auth import get_current_user

router = APIRouter()


@router.get("/api/events")
async def list_system_events(
    request: Request,
    limit: int = 100,
    kind: str | None = None,
    _user: dict = Depends(get_current_user),
):
    """Return the system event trace log (newest first)."""
    store = request.app.state.system_events
    events = await store.list(limit=limit, kind=kind)
    return {"events": events, "count": len(events)}
