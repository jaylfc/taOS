from __future__ import annotations

"""API routes for browser sessions (hybrid neko/CDP tier-2 sessions).

Routes:
  POST   /api/browser/sessions            create a new session
  GET    /api/browser/sessions/{id}       get session (with stream token if running)
  POST   /api/browser/sessions/{id}/terminate  stop a session
"""

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth import get_current_user
from tinyagentos.browser_sessions import pick_browser_node
from tinyagentos.routes.desktop_browser.session_token import mint_session_token

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateSessionBody(BaseModel):
    url: str
    profile: str | None = None


@router.post("/api/browser/sessions")
async def create_session(
    body: CreateSessionBody,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    node = pick_browser_node(request.app.state.cluster_manager)
    if node is None:
        return JSONResponse({"error": "no_capable_node"}, status_code=409)

    mgr = request.app.state.browser_sessions
    session = await mgr.create_session(
        "user", user_id, body.url, body.profile or "default"
    )
    return JSONResponse(session, status_code=201)


@router.get("/api/browser/sessions/{session_id}")
async def get_session(
    session_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    mgr = request.app.state.browser_sessions
    session = await mgr.get_session(session_id)
    if session is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    if session["owner_type"] != "user" or session["owner_id"] != user_id:
        return JSONResponse({"error": "not_found"}, status_code=404)

    if session["status"] == "running" and session.get("neko_url"):
        signing_key = request.app.state.browser_session_signing_key
        _, token = mint_session_token(session_id, user_id, signing_key)
        return {**session, "stream_token": token}

    return dict(session)


@router.post("/api/browser/sessions/{session_id}/terminate")
async def terminate_session(
    session_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    mgr = request.app.state.browser_sessions
    session = await mgr.get_session(session_id)
    if session is None:
        return JSONResponse({"error": "not_found"}, status_code=404)

    if session["owner_type"] != "user" or session["owner_id"] != user_id:
        return JSONResponse({"error": "not_found"}, status_code=404)

    await mgr.terminate_session(session_id)
    return {"ok": True}
