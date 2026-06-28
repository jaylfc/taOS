"""Coding sessions API: record and manage external coding CLI sessions.

A coding session tracks an external coding CLI (Claude Code / Codex / opencode)
launched on the OS to work on a repo. This slice covers data CRUD only.
The launcher (tmux, LXC, cloning) is a later slice.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.coding_sessions.store import VALID_CLIS, VALID_LAUNCH_TARGETS

logger = logging.getLogger(__name__)

router = APIRouter()


class RepoSourceIn(BaseModel):
    kind: str
    value: str


class CodingSessionIn(BaseModel):
    cli: str
    launch_target: str
    workdir: str
    repo_source: RepoSourceIn
    alias: str = ""
    worker: str | None = None
    project_id: str | None = None


class RenameCodingSessionIn(BaseModel):
    alias: str


async def _sync_registry_alias(
    request: Request, session_id: str, alias: str, user: CurrentUser
) -> None:
    """Reflect a renamed session alias in its agent-registry entry. Never raises.

    The session registered itself with handle=session_id and
    display_name=alias, so the rename updates the matching registry row's
    display name to keep the Agents/Registry app in sync.
    """
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is None:
        return
    try:
        rows = (
            await registry.list_all()
            if user.is_admin
            else await registry.list_for_user(user.user_id)
        )
        match = next((r for r in rows if r.get("handle") == session_id), None)
        if match and match.get("canonical_id"):
            await registry.update(match["canonical_id"], display_name=alias)
    except Exception:
        logger.warning(
            "registry alias sync failed for coding session %s", session_id, exc_info=True
        )


@router.post("/api/coding-sessions")
async def create_coding_session(
    body: CodingSessionIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    if body.cli not in VALID_CLIS:
        return JSONResponse(
            {"error": f"cli must be one of {VALID_CLIS}"},
            status_code=400,
        )
    if body.launch_target not in VALID_LAUNCH_TARGETS:
        return JSONResponse(
            {"error": f"launch_target must be one of {VALID_LAUNCH_TARGETS}"},
            status_code=400,
        )
    if body.launch_target == "worker-lxc" and not body.worker:
        return JSONResponse(
            {"error": "worker is required when launch_target is worker-lxc"},
            status_code=400,
        )

    store = request.app.state.coding_session_store
    session = await store.create_session(
        cli=body.cli,
        launch_target=body.launch_target,
        workdir=body.workdir,
        repo_source=body.repo_source.model_dump(),
        created_by=user.user_id,
        alias=body.alias,
        worker=body.worker,
        project_id=body.project_id,
    )

    # Register the session alias in the agent registry so it appears in the
    # Agents/Registry app under its alias. Best-effort: a registry failure must
    # not fail the session create (the session is already persisted).
    #
    # INTEGRATION POINT: AgentRegistryStore.register() is called with
    # framework="coding-session" and display_name=session alias. The returned
    # canonical_id is stored as a log entry; it is not yet written back to the
    # coding_session row (that would need a registry_canonical_id column added
    # in a follow-up). The registry entry points to the coding_session via its
    # handle field (set to the coding_session id).
    _registry = getattr(request.app.state, "agent_registry", None)
    if _registry is not None:
        try:
            await _registry.register(
                framework="coding-session",
                display_name=session["alias"],
                user_id=user.user_id,
                origin="taos-deployed",
                handle=session["id"],
                role="coding-agent",
                capabilities=[],
            )
        except Exception:
            logger.warning(
                "agent registry registration failed for coding session %s",
                session["id"],
                exc_info=True,
            )

    return session


@router.get("/api/coding-sessions")
async def list_coding_sessions(
    request: Request,
    include_archived: bool = False,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.coding_session_store
    items = await store.list_sessions(user.user_id, include_archived=include_archived)
    return {"items": items}


@router.get("/api/coding-sessions/{session_id}")
async def get_coding_session(
    session_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.coding_session_store
    session = await store.get_session(session_id)
    if session is None or (not user.is_admin and session["created_by"] != user.user_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    return session


@router.patch("/api/coding-sessions/{session_id}")
async def rename_coding_session(
    session_id: str,
    body: RenameCodingSessionIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Rename a session's alias (Jay: the launch alias is user-editable)."""
    store = request.app.state.coding_session_store
    session = await store.get_session(session_id)
    if session is None or (not user.is_admin and session["created_by"] != user.user_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    alias = body.alias.strip()
    if not alias:
        return JSONResponse({"error": "alias must be non-empty"}, status_code=400)
    updated = await store.set_alias(session_id, alias)
    await _sync_registry_alias(request, session_id, alias, user)
    return updated


@router.post("/api/coding-sessions/{session_id}/stop")
async def stop_coding_session(
    session_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.coding_session_store
    session = await store.get_session(session_id)
    if session is None or (not user.is_admin and session["created_by"] != user.user_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    updated = await store.set_status(session_id, "stopped")
    return updated


@router.post("/api/coding-sessions/{session_id}/archive")
async def archive_coding_session(
    session_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.coding_session_store
    session = await store.get_session(session_id)
    if session is None or (not user.is_admin and session["created_by"] != user.user_id):
        return JSONResponse({"error": "not found"}, status_code=404)
    updated = await store.archive_session(session_id)
    return updated
