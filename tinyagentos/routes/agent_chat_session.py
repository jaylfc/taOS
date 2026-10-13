"""Self-only chat session routes for registry agents.

A registry agent (for example a self-joined external CLI) authenticates with its
own registry JWT holding the ``chat_session`` grant. The session slug is derived
from the token's canonical_id and never from the request, so an agent can
only drain and answer its own chat session.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from tinyagentos.agent_token_auth import check_agent_scope
from tinyagentos.chat.orphan_reconcile import _CANONICAL_AGENT_ID_RE

logger = logging.getLogger(__name__)

router = APIRouter()


async def _resolve_slug(request: Request) -> str | JSONResponse:
    """Return the caller's session slug, or the error response to send."""
    cid = await check_agent_scope(request, "chat_session")
    if cid is None:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    # The chat router and DM channels name a registry agent by the slug of its
    # canonical_id (display-name based), not by its handle, so the session key
    # must be that same slug or routed messages never reach this stream.
    slug = _CANONICAL_AGENT_ID_RE.sub("", cid)
    if not slug or slug == cid:
        return JSONResponse({"error": "agent has no session slug"}, status_code=403)
    # The bridge keys sessions by the local agent's config ``name``. A guest
    # whose handle equals one would drain and answer that local agent's session.
    config = getattr(request.app.state, "config", None)
    for local in getattr(config, "agents", None) or []:
        if str(local.get("name") or "").strip().lower() == slug.lower():
            return JSONResponse(
                {"error": "handle collides with a local agent"}, status_code=403
            )
    return slug


def _resume_id(request: Request) -> int | None:
    """Last-Event-ID header (or ?since=) as an int; anything else is absent."""
    raw = request.headers.get("last-event-id") or request.query_params.get("since")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _frame_id(frame: str) -> int | None:
    if not frame.startswith("id: "):
        return None
    try:
        return int(frame.split("\n", 1)[0][4:])
    except ValueError:
        return None


@router.get("/api/agents/self/chat/events")
async def self_chat_events(request: Request):
    """SSE stream of the calling agent's own chat events."""
    slug = await _resolve_slug(request)
    if isinstance(slug, JSONResponse):
        return slug
    bridge = getattr(request.app.state, "bridge_sessions", None)
    if bridge is None:
        return JSONResponse({"error": "bridge unavailable"}, status_code=503)

    outbox = getattr(request.app.state, "guest_outbox", None)
    last_id = _resume_id(request)
    # Replay is read before the live subscribe. A message enqueued after this
    # read sits in the session queue, which subscribe() carries over, so it is
    # neither lost nor (via `seen`) delivered twice.
    replay: list = []
    if outbox is not None:
        try:
            replay = await (
                outbox.since(slug, last_id) if last_id is not None
                else outbox.undelivered(slug)
            )
        except Exception:
            logger.exception("guest outbox replay failed for %s", slug)
    seen = {rid for rid, _ in replay}

    async def _delivered(row_id: int) -> None:
        if outbox is None:
            return
        try:
            await outbox.mark_delivered(row_id)
        except Exception:
            logger.exception("guest outbox mark_delivered failed for %s", slug)

    async def event_generator():
        try:
            for row_id, payload in replay:
                yield (
                    f"id: {row_id}\nevent: user_message\n"
                    f"data: {json.dumps(payload)}\n\n"
                )
                await _delivered(row_id)
            async for frame in bridge.subscribe(slug):
                row_id = _frame_id(frame)
                if row_id is not None:
                    if row_id in seen or (last_id is not None and row_id <= last_id):
                        continue
                    seen.add(row_id)
                yield frame
                if row_id is not None:
                    await _delivered(row_id)
                if await request.is_disconnected():
                    break
        except Exception:
            logger.exception("agent chat SSE generator error for %s", slug)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/api/agents/self/chat/reply")
async def self_chat_reply(request: Request):
    """Record a reply from the calling agent under its own slug.

    Any agent/slug/from/author field in the body is ignored for identity.
    """
    slug = await _resolve_slug(request)
    if isinstance(slug, JSONResponse):
        return slug
    bridge = getattr(request.app.state, "bridge_sessions", None)
    if bridge is None:
        return JSONResponse({"error": "bridge unavailable"}, status_code=503)
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    await bridge.record_reply(slug, body)
    return JSONResponse({"accepted": True}, status_code=202)
