from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from tinyagentos.auth_context import CurrentUser, current_user, require_owner_or_admin
from tinyagentos.projects.invite_store import InvitePendingCapError

logger = logging.getLogger(__name__)
router = APIRouter()


class MintInviteIn(BaseModel):
    scopes: list[str] = Field(default_factory=list)
    approval_mode: str = Field(default="auto", pattern="^(auto|manual)$")
    check_interval_secs: int = Field(default=1800, ge=60)


@router.post("/api/projects/{project_id}/invites")
async def mint_invite(
    project_id: str,
    payload: MintInviteIn,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_invites
    project_store = request.app.state.project_store
    project = await project_store.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    require_owner_or_admin(user, project["user_id"])
    try:
        result = await store.mint(
            project_id=project_id,
            scopes=list(payload.scopes),
            approval_mode=payload.approval_mode,
            check_interval_secs=payload.check_interval_secs,
            created_by=user.user_id,
        )
    except InvitePendingCapError as exc:
        return JSONResponse({"error": str(exc)}, status_code=429)
    record = result["record"]
    return {
        "invite_id": record["invite_id"],
        "pin": result["pin"],
        "expires_ts": record["expires_ts"],
        "scopes": record["scopes"],
        "approval_mode": record["approval_mode"],
        "check_interval_secs": record["check_interval_secs"],
    }


@router.get("/api/projects/{project_id}/invites")
async def list_invites(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_invites
    project_store = request.app.state.project_store
    project = await project_store.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    require_owner_or_admin(user, project["user_id"])
    items = await store.list_for_project(project_id)
    return [
        {
            "invite_id": i["invite_id"],
            "scopes": i["scopes"],
            "status": i["status"],
            "expires_ts": i["expires_ts"],
            "redeemed_by": i.get("redeemed_by"),
        }
        for i in items
    ]


@router.delete("/api/projects/{project_id}/invites/{invite_id}")
async def revoke_invite(
    project_id: str,
    invite_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    store = request.app.state.project_invites
    project_store = request.app.state.project_store
    project = await project_store.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    require_owner_or_admin(user, project["user_id"])
    row = await store.get(invite_id)
    if row is None or row.get("project_id") != project_id:
        return JSONResponse({"error": "invite not found"}, status_code=404)
    ok = await store.revoke(invite_id)
    if not ok:
        return JSONResponse({"error": "invite not found or already redeemed"}, status_code=404)
    return JSONResponse(content=None, status_code=204)
