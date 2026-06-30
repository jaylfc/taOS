"""SSE endpoint: GET /api/events/stream

Streams SystemEvents from the EventBus to authenticated clients.  Each frame
is a JSON object with the shape ``{"type": <kind>, "payload": {...}, "ts": <float>}``
so the frontend dispatch table can route by ``type``.

Auth: session cookie via AuthMiddleware (this path is NOT in EXEMPT_PATHS, so
unauthenticated requests are rejected with 401 before they reach the handler).
The handler also checks user_id explicitly to produce a clear error if the
middleware somehow skips it (belt-and-braces).

Reconnect / replay: the EventBus replay buffer (last 32 events per channel)
is delivered to new subscribers automatically on subscribe(), so recent
events are re-streamed on every (re)connect.  This is best-effort, not
precise replay via ``Last-Event-ID``: the ``id:`` field on each frame is a
per-connection counter, not a stable value across reconnects, so the server
does not filter on the ``Last-Event-ID`` request header.  Each event's JSON
payload instead carries a stable ``id`` (the event's trace_id) so the client
can de-dupe events it has already handled.
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


@router.get("/api/events/stream")
async def events_stream(request: Request):
    """SSE stream of system events for the calling user.

    Subscribes to both the per-user channel (``user:<id>``) and the broadcast
    channel on the EventBus and merges them.  Keepalives are sent every 10 s
    so proxies don't close the connection.  Both channel subscriptions are
    cleaned up on disconnect or generator cancellation.
    """
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return JSONResponse({"detail": "Unauthorized"}, status_code=401)

    event_bus = getattr(request.app.state, "event_bus", None)
    if event_bus is None:
        return JSONResponse({"detail": "Service starting"}, status_code=503)

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
        asyncio.create_task(_relay(user_q), name="sse-relay-user"),
        asyncio.create_task(_relay(bcast_q), name="sse-relay-bcast"),
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
                seq += 1
                data = json.dumps(
                    {
                        "type": event.kind,
                        "payload": event.payload,
                        "ts": event.ts,
                        "id": event.trace_id,
                    }
                )
                yield f"id: {seq}\ndata: {data}\n\n"
        finally:
            for t in relay_tasks:
                t.cancel()
            # Await the cancelled relays so they finish unwinding before we
            # unsubscribe -- otherwise a still-running relay can touch a queue
            # that is about to go away, and asyncio logs "Task was destroyed but
            # it is pending". return_exceptions swallows the expected CancelledError.
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
