# tinyagentos/routes/a2a_bus.py
"""Read-only proxy for the external taOSmd A2A coordination bus.

The taOSmd coordination bus (``taosmd serve``) is a SEPARATE service co-located
with the controller (default http://127.0.0.1:7900). It is where cross-product
agents (@taOS, @taOSmd, @hermes) coordinate. This is DISTINCT from taOS's own
internal per-project a2a channels (tinyagentos/projects/a2a.py).

These endpoints proxy the bus: the Messages app can list bus channels and read
messages (read paths), and a registry-authenticated agent (or an admin) can post
a message (POST /api/a2a/bus/send). The raw bus is unauthenticated on the LAN and
trusts its ``from`` field, so the send proxy is the authenticated write path --
an agent posts as its OWN registry handle (scope ``a2a_send``), never able to
spoof another identity or borrow the owner's account. The URL is resolved from
``TAOS_A2A_BUS_URL``.

Bus API (verified live):
  GET  {bus}/a2a/channels
       -> {"channels":[{"channel","members","message_count","created_ts","last_ts"}, ...]}
  GET  {bus}/a2a/messages?thread={channel}&limit={n}
       -> {"messages":[{"id","ts","from","body","thread","reply_to"}, ...]}
  POST {bus}/a2a/send  {"from","thread","body","reply_to"?}
       -> {"id","from","thread","reply_to"}
"""
from __future__ import annotations

import logging
import math
import os

import httpx
import asyncio

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from tinyagentos.agent_token_auth import check_agent_scope

logger = logging.getLogger(__name__)

router = APIRouter()

_DEFAULT_BUS_URL = "http://127.0.0.1:7900"

# Idle-stream heartbeat so intermediaries (and the taOSgo relay) do not reap a
# quiet SSE connection. The design mandates a `: ping` comment every 25s.
_STREAM_HEARTBEAT_SEC = 25


def _bus_url() -> str:
    """Resolve the bus base URL from the environment, trailing slash stripped."""
    return os.environ.get("TAOS_A2A_BUS_URL", _DEFAULT_BUS_URL).rstrip("/")


async def _authorize_bus_read(request: Request) -> None:
    """Gate a bus-read request.

    Admin (session cookie or local token) is allowed unconditionally -- the
    middleware has already set request.state.is_admin for those.  Otherwise the
    caller must present a registry JWT holding an active ``a2a_receive`` grant;
    check_agent_scope raises 401 (bad/malformed token) or 403 (valid token but
    not active / missing scope) and returns None only when no Bearer header is
    present, which is rejected here as 403 (fail closed).
    """
    if getattr(request.state, "is_admin", False):
        return
    caller = await check_agent_scope(request, "a2a_receive")
    if caller is None:
        raise HTTPException(status_code=403, detail="forbidden")


@router.get("/api/a2a/bus/channels")
async def bus_channels(request: Request):
    """List coordination-bus channels, sorted by last activity (newest first).

    Authorized readers: an admin session, the host local token, or an active
    agent registry JWT holding the ``a2a_receive`` scope.

    On any bus error (timeout / connection refused / non-200) this returns an
    empty list with ``available: false`` and HTTP 200, so the frontend can show
    a clean offline state rather than crashing.
    """
    await _authorize_bus_read(request)
    bus = _bus_url()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{bus}/a2a/channels")
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 (degrade to an offline state)
        logger.warning("A2A bus channels fetch failed (%s): %s", bus, exc)
        return JSONResponse({"channels": [], "available": False}, status_code=200)

    channels = data.get("channels", []) if isinstance(data, dict) else []
    channels = sorted(
        channels,
        key=lambda c: c.get("last_ts", 0) or 0,
        reverse=True,
    )
    return {"channels": channels, "available": True}


# Query params this endpoint understands.  Anything else is a 400 rather than a
# silent no-op: an ignored cursor param is indistinguishable from a cursor that
# works, so a reader that passes `since_id=` believes it is reading incrementally
# while it re-reads the whole window forever.  Measured on the live proxy before
# this changed: `since_id=2430` returned 500 messages starting at id 1890.
_MESSAGES_PARAMS = frozenset({"channel", "thread", "limit", "since"})

# Selectors meaning "every thread", not one named channel.  `all` is the idiom
# the raw bus and `taosmd a2a-watch` document, and it exists precisely so a
# reader cannot miss a thread created after it started.  Forwarded here as "omit
# the thread param", which is how the bus itself spells all-threads.
_ALL_CHANNELS = frozenset({"all", "*"})


@router.get("/api/a2a/bus/messages")
async def bus_messages(request: Request):
    """Read messages from the bus, oldest-first as the bus returns them.

    Authorized readers: an admin session, the host local token, or an active
    agent registry JWT holding the ``a2a_receive`` scope.

    ``channel`` is required and maps to the bus ``thread`` query param;
    ``thread`` is accepted as an alias because that is the raw bus's own name
    for it.  ``channel=all`` (or ``*``) reads every thread.  ``limit`` is clamped
    to 1..500.  ``since`` is the cursor and is a message ``ts`` (a float), NOT an
    id -- it is forwarded verbatim and the bus replays everything after it.  Any
    other query param is a 400.  On a bus error this returns an empty list with
    ``available: false`` and HTTP 200.

    A named channel that the bus does not know is reported as ``channel_known:
    false`` alongside the empty list.  Channels on this bus exist only once
    something has been posted to them, so an unknown name and a channel nobody
    has written to yet are the same state -- but a reader that quietly gets a
    200 and nothing else cannot tell a typo from a quiet channel, and stays
    silent forever believing it is connected.
    """
    await _authorize_bus_read(request)

    unknown = sorted(set(request.query_params.keys()) - _MESSAGES_PARAMS)
    if unknown:
        return JSONResponse(
            {
                "error": f"unknown query parameter(s): {', '.join(unknown)}",
                "accepted": sorted(_MESSAGES_PARAMS),
                "hint": "the cursor is 'since' and takes a message ts (float), not an id",
            },
            status_code=400,
        )

    # `thread` is an alias for `channel`, so passing BOTH with different values
    # has no correct interpretation -- and picking one silently drops the other,
    # which is the same "ignored param reads as a working one" failure this
    # endpoint is being fixed for.  Identical values are harmless and allowed.
    chan_param = request.query_params.get("channel") or ""
    thread_param = request.query_params.get("thread") or ""
    if chan_param and thread_param and chan_param != thread_param:
        return JSONResponse(
            {
                "error": (
                    "channel and thread are the same parameter and disagree: "
                    f"channel={chan_param!r} thread={thread_param!r}"
                ),
                "hint": "pass one of them, not both",
            },
            status_code=400,
        )
    channel = chan_param or thread_param
    if not channel:
        return JSONResponse({"error": "channel required"}, status_code=400)

    try:
        limit = int(request.query_params.get("limit", 100))
    except ValueError:
        return JSONResponse({"error": "limit must be an integer"}, status_code=400)
    limit = max(1, min(500, limit))

    since_raw = request.query_params.get("since")
    params: dict = {"limit": limit}
    if channel not in _ALL_CHANNELS:
        params["thread"] = channel
    if since_raw is not None:
        try:
            since_val = float(since_raw)
        except ValueError:
            return JSONResponse(
                {"error": "since must be a message ts (float), not an id"},
                status_code=400,
            )
        # float() happily accepts "nan", "inf" and "-inf".  A NaN cursor makes
        # every comparison on the bus side false, so the reader gets a silent
        # empty window forever and a 200 confirming it -- the exact failure this
        # endpoint is being fixed for, reintroduced through the validator.
        if not math.isfinite(since_val):
            return JSONResponse(
                {"error": "since must be a finite message ts (float), not an id"},
                status_code=400,
            )
        params["since"] = since_val

    bus = _bus_url()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{bus}/a2a/messages",
                params=params,
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 (degrade to an offline state)
        logger.warning("A2A bus messages fetch failed (%s): %s", bus, exc)
        return JSONResponse({"messages": [], "available": False}, status_code=200)

    messages = data.get("messages", []) if isinstance(data, dict) else []
    body: dict = {"messages": messages, "available": True}

    # Only pay for the channel lookup when the answer is empty AND a specific
    # channel was named -- that is the only case where "no messages" is
    # ambiguous, and it keeps the normal read at one bus call.
    if not messages and channel not in _ALL_CHANNELS:
        body["channel_known"] = await _channel_exists(bus, channel)
    return body


async def _channel_exists(bus: str, channel: str) -> bool:
    """True if *channel* appears in the bus channel list.

    Fails OPEN (returns True) when the channel list cannot be fetched: an
    unreachable bus must not be reported to the caller as "your channel name is
    wrong", which would send them chasing a typo that does not exist.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{bus}/a2a/channels")
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("A2A bus channel probe failed (%s): %s", bus, exc)
        return True
    # Fail open on a payload we cannot read, not just on a transport failure.
    # A 200 carrying an error body (or anything that is not the channel list)
    # would otherwise leave `channels` empty and report every channel as
    # unknown -- an accusation of a typo, generated by a bus-side fault, which
    # is precisely the false alarm this probe exists to avoid.
    if not isinstance(data, dict) or "channels" not in data:
        logger.warning(
            "A2A bus channel probe got an unreadable payload from %s: %r", bus, data
        )
        return True
    channels = data.get("channels") or []
    # The bus spells it "channel" in this payload (verified against the live bus:
    # {"channels":[{"channel":"build","members":[...],...}]}) while the SAME
    # concept is "thread" on /a2a/messages. Accept both rather than trust one
    # spelling; an empty name set here would silently report every channel as
    # unknown, which is the exact false alarm this function exists to avoid.
    names = {
        (c.get("channel") or c.get("name") or c.get("thread")) if isinstance(c, dict) else c
        for c in channels
    }
    return channel in names


@router.get("/api/a2a/bus/stream")
async def bus_stream(
    request: Request,
    channel: str = "",
    since: float | None = None,
):
    """Authenticated SSE proxy to the raw bus stream.

    Mirrors the existing messages proxy's auth gate exactly: an admin session,
    the host local token, or an active agent registry JWT holding ``a2a_receive``
    (fail closed). It holds ONE upstream SSE connection to the bus per client and
    relays events verbatim, injecting a ``: ping`` heartbeat comment every 25s so
    idle streams are distinguishable from dead ones and intermediaries do not reap
    them. The raw :7900 bus is never exposed directly.

    ``channel`` maps to the bus ``thread`` query param. Omitting it (or passing
    ``channel=*``) subscribes to ALL threads: no ``thread`` param is forwarded
    upstream, so the bus streams events from every thread. ``since`` maps to the
    bus cursor.
    """
    await _authorize_bus_read(request)

    # An empty channel or "*" means "all threads": omit the thread param
    # upstream so the bus streams every thread. NOTE: when per-channel bus ACLs
    # land (card tsk-dp6fyv), an all-threads subscriber must receive ONLY the
    # threads it is allowed to read, not everything -- filter here, not at the
    # bus. This is the line a future change will get wrong.
    all_threads = channel == "" or channel == "*"
    params: dict = {}
    if not all_threads:
        params["thread"] = channel
    if since is not None:
        params["since"] = since

    bus = _bus_url()

    async def event_stream():
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "GET",
                    f"{bus}/a2a/stream",
                    params=params,
                ) as upstream:
                    heartbeat = asyncio.create_task(
                        _stream_sleep(_STREAM_HEARTBEAT_SEC)
                    )
                    try:
                        async for line in upstream.aiter_lines():
                            if await request.is_disconnected():
                                break
                            # Drain any pending heartbeat that fired while we were
                            # forwarding real data: emit it before the next event.
                            if heartbeat.done():
                                heartbeat.cancel()
                                yield ": ping\n\n"
                                heartbeat = asyncio.create_task(
                                    _stream_sleep(_STREAM_HEARTBEAT_SEC)
                                )
                            if line == "":
                                continue
                            yield f"{line}\n\n"
                    finally:
                        heartbeat.cancel()
        except Exception as exc:  # noqa: BLE001 (surface a final SSE comment)
            logger.warning("A2A bus stream proxy failed (%s): %s", bus, exc)
            yield f": stream error\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_sleep(secs: float) -> None:
    """Sleep for *secs*; cancelled cleanly when a real event arrives first."""
    await asyncio.sleep(secs)


class BusSendBody(BaseModel):
    """A message to post to a coordination-bus thread.

    ``from_`` (JSON key ``from``) is honored ONLY for admin callers, so an
    operator can post as any handle. For an agent-token caller it is ignored and
    the ``from`` is derived from the agent's own registry handle -- one agent can
    never post as another.
    """

    thread: str
    body: str
    reply_to: int | None = None
    from_: str | None = Field(default=None, alias="from")

    model_config = {"populate_by_name": True}


async def _resolve_send_identity(request: Request, body_from: str | None) -> str:
    """Return the bus ``from`` handle authorized for this send, or raise.

    - Admin (session cookie or local token): may set an explicit ``from``
      (operator posts as any handle); defaults to ``@operator`` when omitted.
    - Otherwise the caller must present a registry JWT holding an active
      ``a2a_send`` grant. The ``from`` is DERIVED from that agent's registry
      handle; a client-supplied ``from`` is ignored, so an agent cannot spoof
      another agent's identity. ``check_agent_scope`` raises 401/403; a missing
      Bearer header returns None and is rejected here as 403 (fail closed).
    """
    if getattr(request.state, "is_admin", False):
        # Admin may post as an explicit handle, but keep it a single clean token
        # so it cannot inject newlines/control chars into the bus record or logs.
        handle = (body_from or "").strip()
        handle = "".join(c for c in handle if c.isprintable())[:64].strip()
        return handle or "@operator"

    caller = await check_agent_scope(request, "a2a_send")
    if caller is None:
        raise HTTPException(status_code=403, detail="forbidden")

    registry = getattr(request.app.state, "agent_registry", None)
    record = await registry.get(caller) if registry is not None else None
    handle = ((record or {}).get("handle") or "").strip()
    if not handle:
        # An active a2a_send grant with no bus handle cannot be safely attributed.
        raise HTTPException(status_code=403, detail="agent has no bus handle")
    return handle


@router.post("/api/a2a/bus/send")
async def bus_send(request: Request, body: BusSendBody):
    """Post a message to a coordination-bus thread as the authenticated identity.

    Authorized senders: an admin session / host local token (may set ``from``),
    or an active agent registry JWT holding the ``a2a_send`` scope (``from`` is
    forced to the agent's own handle). This is the authenticated write path so
    agents post as themselves instead of sharing the owner's account.

    Unlike the read endpoints, a bus failure surfaces as 502: a caller must know
    when its message did not land.
    """
    from_handle = await _resolve_send_identity(request, body.from_)

    thread = body.thread.strip()
    text = body.body.strip()
    if not thread or not text:
        return JSONResponse({"error": "thread and body required"}, status_code=400)
    # reply_to is a bus message id (a SQLite rowid): must be a positive integer
    # within the signed-64-bit id space. The bus owns id existence; the proxy
    # only rejects values that could never be a real id.
    if body.reply_to is not None and not (0 < body.reply_to <= 2**63 - 1):
        return JSONResponse(
            {"error": "reply_to must be a positive message id"}, status_code=400
        )

    payload: dict = {"from": from_handle, "thread": thread, "body": text}
    if body.reply_to is not None:
        payload["reply_to"] = body.reply_to

    bus = _bus_url()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(f"{bus}/a2a/send", json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        logger.warning("A2A bus send failed (%s): %s", bus, exc)
        raise HTTPException(status_code=502, detail="a2a bus unavailable")

    return {"ok": True, "from": from_handle, "message": data}
