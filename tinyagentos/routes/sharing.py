from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth import get_current_user

router = APIRouter()


class GrantBody(BaseModel):
    artifact_id: str
    artifact_kind: str
    grantee: str


async def _require_owner(request: Request, grant_id: str, current_user: dict = Depends(get_current_user)) -> str:
    """Verify the authed user owns the grant. Returns owner_id on success."""
    owner_id = current_user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    store = request.app.state.sharing_grants
    grant = await store.get(grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="grant not found")
    if grant["owner_id"] != owner_id:
        raise HTTPException(status_code=403, detail="only the owner may revoke this grant")
    return owner_id


@router.post("/api/sharing/grants")
async def create_grant(request: Request, body: GrantBody, current_user: dict = Depends(get_current_user)):
    owner_id = current_user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    artifact_id = body.artifact_id.strip()
    artifact_kind = body.artifact_kind.strip()
    grantee = body.grantee.strip()

    if not artifact_id:
        return JSONResponse({"error": "artifact_id is required"}, status_code=400)
    if artifact_kind not in ("app", "game", "project", "workflow", "studio"):
        return JSONResponse({"error": "invalid artifact_kind"}, status_code=400)
    if not grantee:
        return JSONResponse({"error": "grantee is required"}, status_code=400)

    store = request.app.state.sharing_grants
    try:
        grant = await store.grant(artifact_id, artifact_kind, owner_id, grantee)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    return grant


@router.delete("/api/sharing/grants/{grant_id}")
async def revoke_grant(request: Request, grant_id: str, owner_id: str = Depends(_require_owner)):
    store = request.app.state.sharing_grants
    grant = await store.revoke(grant_id)
    if grant is None:
        raise HTTPException(status_code=404, detail="grant not found or already revoked")
    return {"ok": True, "grant": grant}


@router.get("/api/sharing/grants/mine")
async def list_owned_grants(request: Request, current_user: dict = Depends(get_current_user)):
    owner_id = current_user.get("id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    store = request.app.state.sharing_grants
    grants = await store.list_owned(owner_id)
    return grants


@router.get("/api/sharing/grants/shared-with-me")
async def list_shared_with_me(request: Request, current_user: dict = Depends(get_current_user)):
    username = current_user.get("username") or ""
    email = current_user.get("email") or ""

    store = request.app.state.sharing_grants
    grants = []
    seen_ids = set()

    if username:
        for g in await store.list_for(username):
            if g["id"] not in seen_ids:
                grants.append(g)
                seen_ids.add(g["id"])

    if email and email != username:
        for g in await store.list_for(email):
            if g["id"] not in seen_ids:
                grants.append(g)
                seen_ids.add(g["id"])

    grants.sort(key=lambda g: g["created_at"], reverse=True)
    return grants