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


async def _caller_owns_artifact(
    request: Request, 
    artifact_kind: str, 
    artifact_id: str, 
    owner_id: str
) -> bool:
    """Verify that the authenticated user owns the specified artifact.
    
    Returns True if the user owns the artifact, False otherwise.
    """
    # Check for app and game artifacts - use the app store if available
    if artifact_kind in ("app", "game"):
        app_store = getattr(request.app.state, "app_grants", None)
        if app_store is not None:
            # For apps and games, check if the app belongs to the user
            # This depends on the actual schema of the app store
            # For now, we'll check the app_grants store for ownership
            try:
                # Check if the user has granted capabilities for this app
                # If they have, they likely own or have permission to share it
                capabilities = await app_store.granted_capabilities(owner_id, artifact_id)
                return bool(capabilities)
            except Exception:
                # If there's an error checking, assume no ownership
                return False
    
    # Check for project artifacts
    elif artifact_kind == "project":
        project_store = getattr(request.app.state, "project_store", None)
        if project_store is not None:
            project = await project_store.get_project(artifact_id)
            if project is not None:
                # Check if the project is owned by the user (user_id field per requirements)
                return project.get("created_by") == owner_id or project.get("user_id") == owner_id
        return False
    
    # Check for workflow artifacts
    elif artifact_kind == "workflow":
        # Check if workflow store exists with an owner field
        workflow_store = getattr(request.app.state, "workflow_store", None)
        if workflow_store is not None:
            # TODO: Implement workflow ownership check when workflow store exists
            return False
        return False
    
    # Check for studio artifacts  
    elif artifact_kind == "studio":
        # Check if studio store exists with an owner field
        studio_store = getattr(request.app.state, "studio_store", None)
        if studio_store is not None:
            # TODO: Implement studio ownership check when studio store exists
            return False
        return False
    
    return False


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

    # Verify that the caller owns the artifact before creating a grant
    if not await _caller_owns_artifact(request, artifact_kind, artifact_id, owner_id):
        return JSONResponse({"error": "you do not own this artifact"}, status_code=403)

    store = request.app.state.sharing_grants
    try:
        grant = await store.grant(artifact_id, artifact_kind, owner_id, grantee)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except PermissionError as exc:
        return JSONResponse({"error": str(exc)}, status_code=403)
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