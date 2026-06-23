"""Per-app capability grant API (app permission system, #56).

The API surface over AppGrantsStore: a user reviews, grants/denies, and revokes
the capabilities an installed app holds. Decision 6 (manifest declares + runtime
grants via the Decisions/consent flow); this is the runtime-grant layer.

Vocabulary-agnostic: capability names are passed through as strings. Validating
them against the closed APP_CAPABILITIES vocabulary is a separate slice (pending
Jay's v1 vocabulary, pending-decisions item 23), so this surface does not gate
on that open question. Grants are scoped to the calling user.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import CurrentUser, current_user

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
