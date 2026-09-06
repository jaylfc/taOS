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
precise replay via Last-Event-ID -- and so frames deliberately carry NO SSE
``id:`` line, which is what would make a browser send Last-Event-ID at all.

Backpressure: at most 256 events are buffered per connection.  A client that
falls further behind loses the OLDEST buffered events and is then sent a
``{"kind": "events.lagged", "dropped": N}`` frame, its cue to refetch state
rather than assume it saw everything.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter
from fastapi.requests import Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Upper bound on events buffered for one connection. The bus itself hands out
# unbounded queues, so without a cap a client that stops reading grows the
# process's memory with every event forever.
_MERGED_MAXSIZE = 256


async def _relay(
    src: asyncio.Queue, dst: asyncio.Queue, lag: dict, allowed_kinds: set | None = None
) -> None:
    """Move events from a channel queue into the merged queue, never blocking.

    When *dst* is full the OLDEST event is discarded to make room and the
    drop is counted, so a slow client degrades into a gap it is told about
    rather than stalling the relay (which would silently stop delivery for
    the whole connection and pin both channel queues in memory).

    The kind filter is applied HERE rather than at the yield, so an unwanted
    kind never occupies one of the bounded slots. Filtering later would let a
    busy unrelated kind evict the events this subscriber actually asked for
    and report a lag that, from the subscriber's point of view, never happened.
    """
    while True:
        ev = await src.get()
        if allowed_kinds is not None and ev.kind not in allowed_kinds:
            continue
        while True:
            try:
                dst.put_nowait(ev)
                break
            except asyncio.QueueFull:
                try:
                    dst.get_nowait()
                    lag["dropped"] += 1
                except asyncio.QueueEmpty:  # pragma: no cover - dst cannot be both full and empty
                    break


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

    # An all-whitespace kinds parameter is truthy but names no kind. Deriving
    # the set FIRST and treating an empty one as "no filter" keeps `?kinds=` and
    # `?kinds=   ` behaving the way the docstring promises; testing the raw
    # string instead produced an empty allowlist that matched nothing, so the
    # stream silently delivered zero events.
    kinds_param = request.query_params.get("kinds", "")
    _requested = {k.strip() for k in kinds_param.split(",") if k.strip()}
    allowed_kinds = _requested or None

    user_ch = f"user:{user_id}"

    async def gen():
        # Subscribe and start the relays INSIDE the generator, not in the
        # handler body. An async generator that is closed without ever being
        # iterated never runs its body, so a finally in here can only undo
        # setup that also happened in here. Done in the handler, a client that
        # disconnects between the handler returning and StreamingResponse
        # starting to stream leaked two EventBus subscriptions and two
        # never-cancelled relay tasks per occurrence -- and the bus keeps
        # feeding those queues forever, since nothing ever drains them.
        user_q = None
        bcast_q = None
        relay_tasks: list[asyncio.Task] = []
        try:
            user_q = await event_bus.subscribe(user_ch)
            bcast_q = await event_bus.subscribe("broadcast")

            # Merge both channels into a single queue so the generator has one
            # await. Bounded: see _relay for the overflow behaviour.
            merged: asyncio.Queue = asyncio.Queue(maxsize=_MERGED_MAXSIZE)
            lag = {"dropped": 0}

            relay_tasks = [
                asyncio.create_task(
                    _relay(user_q, merged, lag, allowed_kinds),
                    name="os-events-relay-user",
                ),
                asyncio.create_task(
                    _relay(bcast_q, merged, lag, allowed_kinds),
                    name="os-events-relay-bcast",
                ),
            ]

            while True:
                if await request.is_disconnected():
                    return
                try:
                    event = await asyncio.wait_for(merged.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    yield ":keepalive\n\n"
                    continue
                if lag["dropped"]:
                    dropped, lag["dropped"] = lag["dropped"], 0
                    yield "data: " + json.dumps(
                        {
                            "kind": "events.lagged",
                            "id": None,
                            "ts": time.time(),
                            "dropped": dropped,
                        }
                    ) + "\n\n"
                # No kind filter here: _relay already dropped everything this
                # subscriber did not ask for, before it could take a slot.
                data = json.dumps(
                    {
                        "kind": event.kind,
                        "id": event.trace_id,
                        "ts": event.ts,
                    }
                )
                # No SSE "id:" line: emitting one makes a browser send
                # Last-Event-ID on reconnect, which this endpoint ignores --
                # resume is best-effort via the bus replay buffer.
                yield f"data: {data}\n\n"
        finally:
            for t in relay_tasks:
                t.cancel()
            if relay_tasks:
                await asyncio.gather(*relay_tasks, return_exceptions=True)
            if user_q is not None:
                await event_bus.unsubscribe(user_ch, user_q)
            if bcast_q is not None:
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
