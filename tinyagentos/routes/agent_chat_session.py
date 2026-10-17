"""Self-only chat session routes for registry agents.

A registry agent (for example a self-joined external CLI) authenticates with its
own registry JWT holding the ``chat_session`` grant. The session slug is derived
from the token's canonical_id and never from the request, so an agent can
only drain and answer its own chat session.
"""
from __future__ import annotations

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


@router.get("/api/agents/self/chat/events")
async def self_chat_events(request: Request):
    """SSE stream of the calling agent's own chat events."""
    slug = await _resolve_slug(request)
    if isinstance(slug, JSONResponse):
        return slug
    bridge = getattr(request.app.state, "bridge_sessions", None)
    if bridge is None:
        return JSONResponse({"error": "bridge unavailable"}, status_code=503)

    async def event_generator():
        try:
            async for frame in bridge.subscribe(slug):
                yield frame
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
