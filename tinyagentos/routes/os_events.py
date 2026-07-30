"""SSE endpoint: GET /api/os/events

Streams typed OS-level change events from the EventBus.  Each frame carries
only the event kind and id -- NEVER the payload -- so the client can react
to changes without learning the contents.

Auth: session cookie via AuthMiddleware (this path is NOT in EXEMPT_PATHS, so
unauthenticated requests are rejected with 401 before they reach the handler).
The handler also checks user_id explicitly to produce a clear error if the
middleware somehow skips it (belt-and-braces).

Query params:
  kinds: comma-separated list of event kinds to subscribe to (e.g.
         "projects.task.changed,agents.status.changed,notifications.new").
         An empty or missing kinds parameter means "subscribe to all".

Reconnect / resume: the EventBus replay buffer (last 32 events per channel)
is delivered to new subscribers automatically on subscribe(), so recent
events are re-streamed on every (re)connect.  This is best-effort, not
precise replay via Last-Event-ID.
"""
from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/os/events")
async def os_events(request: Request):
    """SSE stream of typed OS change events for the calling user.

    Subscribes to both the per-user channel (``user:<id>``) and the broadcast
    channel on the EventBus and merges them.  Keepalives are sent every 10 s
    so proxies don't close the connection.  Both channel subscriptions are
    cleaned up on disconnect or generator cancellation.

    The ``kinds`` query parameter limits events to a comma-separated list of
    kinds; an empty or omitted parameter means "all kinds".  Events are
    emitted with only ``kind`` and ``id`` fields -- never the payload.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    event_bus = getattr(request.app.state, "event_bus", None)
    if event_bus is None:
        return JSONResponse({"detail": "Service starting"}, status_code=503)

    kinds_param = request.query_params.get("kinds", "")
    allowed_kinds = (
        {k.strip() for k in kinds_param.split(",") if k.strip()}
        if kinds_param
        else None
    )

    user_ch = f"user:{user_id}"
    user_q = await event_bus.subscribe(user_ch)
    bcast_q = await event_bus.subscribe("broadcast")

    # Merge both channels into a single queue so the generator has one await.
    merged: asyncio.Queue = asyncio.Queue()

    async def _relay(src: asyncio.Queue) -> None:
        while True:
            ev = await src.get()
            await merged.put(ev)

    relay_tasks = [
        asyncio.create_task(_relay(user_q), name="os-events-relay-user"),
        asyncio.create_task(_relay(bcast_q), name="os-events-relay-bcast"),
    ]

    async def gen():
        seq = 0
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(merged.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    yield ":keepalive\n\n"
                    continue
                if allowed_kinds is not None and event.kind not in allowed_kinds:
                    continue
                seq += 1
                data = json.dumps(
                    {
                        "kind": event.kind,
                        "id": event.trace_id,
                        "ts": event.ts,
                    }
                )
                yield f"id: {seq}\ndata: {data}\n\n"
        finally:
            for t in relay_tasks:
                t.cancel()
            await asyncio.gather(*relay_tasks, return_exceptions=True)
            await event_bus.unsubscribe(user_ch, user_q)
            await event_bus.unsubscribe("broadcast", bcast_q)

    # Cache-Control: no-cache + X-Accel-Buffering: no prevent nginx/proxies
    # from buffering the stream (which would coalesce or delay events).
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
