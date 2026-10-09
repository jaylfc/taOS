# tinyagentos/routes/agent_chat_session.py
"""Self-only chat-session routes for registry agents (taOStalk S1).

A registry agent (self-joined external CLI, seat) reaches the openclaw bridge
through these two routes instead of the host-local-token-gated
``/api/openclaw/sessions/{agent}/events`` and ``.../reply``. Both authenticate
with the agent's OWN Ed25519 registry JWT holding the ``chat_session`` grant,
and both are SELF-ONLY BY CONSTRUCTION: the slug is derived from the token's
``sub`` (the registry canonical_id) by looking up the agent's own registry row,
never from a request-body field. An agent can therefore only subscribe to and
reply as ITSELF.

  GET  /api/agents/self/chat/events  -> text/event-stream over bridge_sessions.subscribe(slug)
  POST /api/agents/self/chat/reply  -> bridge_sessions.record_reply(slug, body)

Identity fields in the reply body (``agent``, ``slug``, ``from``, ``author``)
are IGNORED: the slug comes only from the token.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from tinyagentos.agent_token_auth import check_agent_scope

logger = logging.getLogger(__name__)

router = APIRouter()


async def _slug_from_token(request: Request, cid: str) -> str:
    """Resolve the chat slug for *cid* from the agent's own registry row.

    Mirrors ``routes/a2a_bus.py _resolve_send_identity``: the handle is read
    from the registry record for the token's ``sub`` (canonical_id), and the
    leading ``@`` is stripped so the slug matches what
    ``BridgeSessionRegistry`` keys on. Raises 403 when the agent has no
    handle -- an incomplete chat identity, exactly like the bus path.
    """
    registry = getattr(request.app.state, "agent_registry", None)
    record = await registry.get(cid) if registry is not None else None
    handle = ((record or {}).get("handle") or "").strip()
    if not handle:
        raise HTTPException(status_code=403, detail="agent has no chat handle")
    return handle[1:] if handle.startswith("@") else handle


@router.get("/api/agents/self/chat/events")
async def self_chat_events(request: Request):
    """SSE stream of chat events for the authenticated agent's OWN slug.

    Requires a registry JWT holding an active ``chat_session`` grant. The slug
    is derived from the token's own registry identity; an agent can only ever
    subscribe to itself.
    """
    cid = await check_agent_scope(request, "chat_session")
    if cid is None:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    registry = getattr(request.app.state, "bridge_sessions", None)
    if registry is None:
        return JSONResponse({"error": "bridge unavailable"}, status_code=503)

    slug = _slug_from_token(request, cid)

    async def event_generator():
        try:
            async for frame in registry.subscribe(slug):
                yield frame
                if await request.is_disconnected():
                    break
        except Exception:
            logger.exception("self chat SSE generator error for agent %s", slug)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/agents/self/chat/reply")
async def self_chat_reply(request: Request):
    """Receive a reply payload for the authenticated agent's OWN slug.

    Requires a registry JWT holding an active ``chat_session`` grant. The slug
    is derived from the token's own registry identity; any ``agent``, ``slug``,
    ``from`` or ``author`` field in the body is IGNORED for identity, so an
    agent cannot reply as another agent.
    """
    cid = await check_agent_scope(request, "chat_session")
    if cid is None:
        return JSONResponse({"error": "forbidden"}, status_code=403)

    registry = getattr(request.app.state, "bridge_sessions", None)
    if registry is None:
        return JSONResponse({"error": "bridge unavailable"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)

    slug = _slug_from_token(request, cid)

    # Self-only: identity comes from the token, never from the body. Strip any
    # client-supplied identity fields so they cannot influence routing.
    for field in ("agent", "slug", "from", "author"):
        body.pop(field, None)

    await registry.record_reply(slug, body)
    return JSONResponse({"accepted": True}, status_code=202)