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
from tinyagentos.browser_sessions import BrowserWorkerError, list_browser_nodes, pick_browser_node
from tinyagentos.routes.desktop_browser.session_token import mint_session_token

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateSessionBody(BaseModel):
    url: str
    profile: str | None = None
    node: str | None = None


@router.post("/api/browser/sessions")
async def create_session(
    body: CreateSessionBody,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    cluster = request.app.state.cluster_manager
    if body.node is not None:
        capable_names = {n["name"] for n in list_browser_nodes(cluster)}
        if body.node not in capable_names:
            return JSONResponse({"error": "no_capable_node"}, status_code=409)
        node = body.node
    else:
        node = pick_browser_node(cluster)
        if node is None:
            return JSONResponse({"error": "no_capable_node"}, status_code=409)

    worker = cluster.get_worker(node)
    if worker is None:
        return JSONResponse({"error": "no_capable_node"}, status_code=409)

    mgr = request.app.state.browser_sessions
    session = await mgr.create_session(
        "user", user_id, body.url, body.profile or "default"
    )
    auth_token = getattr(request.app.state, "browser_worker_auth_token", None)
    try:
        session = await mgr.start_on_worker(
            session["id"],
            node=node,
            worker_url=worker.url,
            profile_volume=f"taos-browser-{session['id']}",
            auth_token=auth_token,
        )
    except BrowserWorkerError:
        return JSONResponse({"error": "worker_start_failed"}, status_code=502)
    return JSONResponse(session, status_code=201)


@router.get("/api/browser/nodes")
async def get_browser_nodes(
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    nodes = list_browser_nodes(request.app.state.cluster_manager)
    return {"nodes": nodes}


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

    cluster = request.app.state.cluster_manager
    auth_token = getattr(request.app.state, "browser_worker_auth_token", None)
    if session.get("node") and session.get("container_id"):
        worker = cluster.get_worker(session["node"])
        if worker is not None:
            await mgr.stop_on_worker(
                session_id,
                worker_url=worker.url,
                container_id=session["container_id"],
                auth_token=auth_token,
            )
        else:
            await mgr.terminate_session(session_id)
    else:
        await mgr.terminate_session(session_id)
    return {"ok": True}
