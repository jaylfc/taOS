"""API routes for the Zero-Loss Archive (taOSmd)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter()


class RecordEventRequest(BaseModel):
    event_type: str
    data: dict = {}
    agent_name: str | None = None
    app_id: str | None = None
    summary: str = ""


class TrackingToggle(BaseModel):
    enabled: bool


@router.post("/api/archive/record")
async def record_event(request: Request, body: RecordEventRequest):
    store = request.app.state.archive
    row_id = await store.record(
        event_type=body.event_type,
        data=body.data,
        agent_name=body.agent_name,
        app_id=body.app_id,
        summary=body.summary,
    )
    if row_id == -1:
        return {"status": "skipped", "reason": "user tracking disabled"}
    return {"id": row_id, "status": "recorded"}


@router.get("/api/archive/events")
async def query_events(
    request: Request,
    event_type: str | None = None,
    agent_name: str | None = None,
    app_id: str | None = None,
    since: float | None = None,
    until: float | None = None,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    store = request.app.state.archive
    events = await store.query(
        event_type=event_type,
        agent_name=agent_name,
        app_id=app_id,
        since=since,
        until=until,
        search=search,
        limit=limit,
        offset=offset,
    )
    return {"events": events, "count": len(events)}


@router.get("/api/archive/events/{event_id}")
async def get_event(request: Request, event_id: int):
    store = request.app.state.archive
    event = await store.get_event(event_id)
    if not event:
        from fastapi.responses import JSONResponse
        return JSONResponse({"error": "not found"}, status_code=404)
    return event


@router.get("/api/archive/stats")
async def archive_stats(request: Request):
    store = request.app.state.archive
    return await store.stats()


@router.get("/api/archive/daily")
async def daily_summary(request: Request, date: str | None = None):
    store = request.app.state.archive
    return await store.daily_summary(date=date)


@router.get("/api/archive/export/{date}")
async def export_day(request: Request, date: str):
    store = request.app.state.archive
    events = await store.export_day(date)
    return {"date": date, "events": events, "count": len(events)}


@router.post("/api/archive/tracking")
async def set_tracking(request: Request, body: TrackingToggle):
    store = request.app.state.archive
    await store.set_user_tracking(body.enabled)
    return {"user_tracking_enabled": body.enabled}


@router.get("/api/archive/tracking")
async def get_tracking(request: Request):
    store = request.app.state.archive
    return {"user_tracking_enabled": store.user_tracking_enabled}


@router.post("/api/archive/compress")
async def compress_old(request: Request, days_old: int = 1):
    store = request.app.state.archive
    compressed = await store.compress_old_files(days_old)
    return {"compressed": compressed}
