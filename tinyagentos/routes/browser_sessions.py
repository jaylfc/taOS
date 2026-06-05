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
from tinyagentos.browser_sessions import (
    BrowserWorkerError,
    list_browser_nodes,
    pick_browser_node,
    resolve_browser_target,
)
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


@router.get("/api/browser/sessions/mine")
async def get_my_session(
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Return the caller's always-on browser session, creating and starting it if needed.

    Placement order: host (if RAM-capable) -> best cluster worker -> 409.
    When running with a neko_url, attaches a short-lived stream_token.

    NOTE: app.state.browser_container_runner and app.state.host_hardware must be
    wired in app setup before this route is used in production (follow-up task).
    """
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    mgr = request.app.state.browser_sessions
    session = await mgr.get_or_create_mine(user_id)

    if session["status"] in ("pending", "idle"):
        cluster = request.app.state.cluster_manager
        host_hw = getattr(request.app.state, "host_hardware", None)
        target = resolve_browser_target(cluster, host_hw)
        if target is None:
            return JSONResponse({"error": "no_capable_node"}, status_code=409)
        kind, node = target
        vol = f"taos-browser-{session['id']}"
        try:
            if kind == "host":
                runner = request.app.state.browser_container_runner
                session = await mgr.start_on_host(session["id"], profile_volume=vol, runner=runner)
            else:
                worker = cluster.get_worker(node)
                auth_token = getattr(request.app.state, "browser_worker_auth_token", None)
                session = await mgr.start_on_worker(
                    session["id"],
                    node=node,
                    worker_url=worker.url,
                    profile_volume=vol,
                    auth_token=auth_token,
                )
        except BrowserWorkerError:
            return JSONResponse({"error": "worker_start_failed"}, status_code=502)

    if session.get("status") == "running" and session.get("neko_url"):
        signing_key = request.app.state.browser_session_signing_key
        _, token = mint_session_token(session["id"], user_id, signing_key)
        return JSONResponse({**session, "stream_token": token})

    return JSONResponse(session)


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


class MigrateBody(BaseModel):
    target: str


@router.post("/api/browser/sessions/{session_id}/migrate")
async def migrate_session(
    session_id: str,
    body: MigrateBody,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Move a running session to ``target`` (host or a capable worker).

    Graceful suspend -> move profile -> resume, emitting session_migrating /
    session_resumed so agents on the session pause and await reconnection.

    NOTE: the real cross-node profile-volume transfer and the agent-signal
    broadcast channel are confirmed by the host<->Fedora integration round-trip
    (sub-plan F Task 5). Here ``move_volume`` and ``emit`` are best-effort +
    logged; the orchestration + transitions are what these routes exercise.
    """
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
    target = body.target
    capable = {n["name"] for n in list_browser_nodes(cluster)}
    if target != "host" and target not in capable:
        return JSONResponse({"error": "no_capable_node", "target": target}, status_code=409)

    runner = getattr(request.app.state, "browser_container_runner", None)
    auth_token = getattr(request.app.state, "browser_worker_auth_token", None)
    vol = f"taos-browser-{session_id}"

    async def emit(kind: str, payload: dict) -> None:
        # Best-effort lifecycle signal; the broadcast channel agents subscribe to
        # is confirmed during the F Task-5 round-trip.
        logger.info("browser session %s: %s %s", session_id, kind, payload)

    async def stop_source(sess: dict) -> None:
        node = sess.get("node") or "host"
        if node == "host":
            if runner is not None and sess.get("container_id"):
                await runner.stop(container_id=sess["container_id"])
        else:
            worker = cluster.get_worker(node)
            if worker is not None and sess.get("container_id"):
                await mgr.stop_on_worker(
                    sess["id"], worker_url=worker.url,
                    container_id=sess["container_id"], auth_token=auth_token,
                    set_status=None,
                )

    async def move_volume(volume: str, src_node: str, dst_node: str) -> None:
        # Real cross-node tar export/import is wired + verified in F Task 5.
        logger.warning(
            "browser session %s: profile volume %s move %s->%s pending integration",
            session_id, volume, src_node, dst_node,
        )

    async def start_target(sess: dict, tgt: str) -> dict:
        if tgt == "host":
            return await mgr.start_on_host(sess["id"], profile_volume=vol, runner=runner)
        worker = cluster.get_worker(tgt)
        return await mgr.start_on_worker(
            sess["id"], node=tgt, worker_url=worker.url,
            profile_volume=vol, auth_token=auth_token,
        )

    try:
        refreshed = await mgr.migrate_session(
            session_id, target=target,
            stop_source=stop_source, move_volume=move_volume,
            start_target=start_target, emit=emit,
        )
    except BrowserWorkerError:
        return JSONResponse({"error": "migration_failed"}, status_code=502)

    if refreshed is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    if refreshed.get("status") == "running" and refreshed.get("neko_url"):
        signing_key = request.app.state.browser_session_signing_key
        _, token = mint_session_token(session_id, user_id, signing_key)
        return {**refreshed, "stream_token": token}
    return dict(refreshed)
