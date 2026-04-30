"""Worker /redeem endpoint — validates HMAC ticket, sets cookie, 302."""
from __future__ import annotations

import secrets
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from tinyagentos.shortcuts.tickets import validate_ticket, _GLOBAL_JTI_TRACKER
from tinyagentos.cluster.worker_registry import get_local_worker

router = APIRouter()

# In-memory session store: session_id -> {agent_id, shortcut_idx, scope, expires_at}
_sessions: dict[str, dict[str, Any]] = {}
_SESSION_IDLE_TTL = 300  # 5 minutes


def _new_session(agent_id: str, shortcut_idx: int, scope: str) -> str:
    session_id = secrets.token_urlsafe(32)
    _sessions[session_id] = {
        "agent_id": agent_id,
        "shortcut_idx": shortcut_idx,
        "scope": scope,
        "expires_at": time.monotonic() + _SESSION_IDLE_TTL,
    }
    return session_id


def _get_session(session_id: str) -> dict[str, Any]:
    """Return session or raise HTTPException(401)."""
    session = _sessions.get(session_id)
    if session is None:
        raise HTTPException(status_code=401, detail="Session not found or expired")
    if time.monotonic() > session["expires_at"]:
        del _sessions[session_id]
        raise HTTPException(status_code=401, detail="Session expired")
    session["expires_at"] = time.monotonic() + _SESSION_IDLE_TTL
    return session


def _scope_to_path(scope: str, agent_id: str, shortcut_idx: int) -> str:
    if scope == "dashboard":
        return f"/shortcut/dashboard/{agent_id}/{shortcut_idx}/"
    return f"/shortcut/terminal/{agent_id}/{shortcut_idx}"


@router.get("/redeem")
async def redeem_ticket(
    t: str = Query(..., description="Base64url-encoded HMAC ticket"),
) -> RedirectResponse:
    """Validate ticket, set session cookie, redirect to the shortcut endpoint."""
    worker = get_local_worker()
    signing_key: bytes = worker["signing_key"]

    try:
        ticket = validate_ticket(t, signing_key=signing_key, tracker=_GLOBAL_JTI_TRACKER)
    except ValueError as exc:
        msg = str(exc)
        if "expired" in msg:
            detail = "ticket expired"
        elif "replay" in msg.lower() or "replayed" in msg.lower():
            detail = "replay detected"
        else:
            detail = "invalid ticket"
        raise HTTPException(status_code=401, detail=detail) from exc

    session_id = _new_session(
        agent_id=ticket.agent_id,
        shortcut_idx=ticket.shortcut_idx,
        scope=ticket.scope,
    )
    location = _scope_to_path(ticket.scope, ticket.agent_id, ticket.shortcut_idx)

    response = RedirectResponse(url=location, status_code=302)
    response.set_cookie(
        key="taos_shortcut",
        value=session_id,
        httponly=True,
        samesite="lax",
        max_age=_SESSION_IDLE_TTL,
        path="/",
    )
    return response
