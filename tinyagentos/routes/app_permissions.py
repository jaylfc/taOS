"""Per-app capability grant API (app permission system, #56).

The API surface over AppGrantsStore: a user reviews, grants/denies, and revokes
the capabilities an installed app holds. Decision 6 (manifest declares + runtime
grants via the Decisions/consent flow); this is the runtime-grant layer.

Grants are validated against the closed capability vocabulary in
tinyagentos/userspace/capabilities.py (the same source of truth the broker
enforces and the package parser validates manifests against), so a grant can
never record a typo'd or made-up capability. Grants are scoped to the calling
user.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.userspace.capabilities import is_known_capability

router = APIRouter()


class GrantIn(BaseModel):
    capability: str
    decision: str = "granted"  # "granted" or "denied"


class RevokeIn(BaseModel):
    capability: str


@router.get("/api/apps/{app_id}/permissions")
async def list_app_permissions(
    app_id: str, request: Request, user: CurrentUser = Depends(current_user)
):
    """The current user's capability decisions for an app, plus the granted set."""
    store = request.app.state.app_grants
    grants = await store.list_grants(user.user_id, app_id)
    granted = sorted(await store.granted_capabilities(user.user_id, app_id))
    return {"app_id": app_id, "grants": grants, "granted": granted}


@router.post("/api/apps/{app_id}/permissions")
async def set_app_permission(
    app_id: str, body: GrantIn, request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Grant or deny a capability for an app on behalf of the calling user."""
    store = request.app.state.app_grants
    cap = body.capability.strip()
    if not cap:
        return JSONResponse({"error": "capability required"}, status_code=400)
    if not is_known_capability(cap):
        return JSONResponse(
            {"error": f"unknown capability: {cap}"}, status_code=400
        )
    try:
        rec = await store.set_decision(user.user_id, app_id, cap, decision=body.decision)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"grant": rec}


@router.post("/api/apps/{app_id}/permissions/revoke")
async def revoke_app_permission(
    app_id: str, body: RevokeIn, request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Revoke a capability (records a denial, keeps the row)."""
    store = request.app.state.app_grants
    cap = body.capability.strip()
    if not cap:
        return JSONResponse({"error": "capability required"}, status_code=400)
    await store.revoke(user.user_id, app_id, cap)
    return {"ok": True}
