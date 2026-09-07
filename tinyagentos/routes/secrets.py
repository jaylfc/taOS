from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import (
    CurrentUser,
    current_user,
    require_admin,
    require_agent_owner_or_admin,
    require_owner_or_admin,
)

# The keystore is system-global (one store, plaintext values on read), so every
# handler is admin-or-local-token EXCEPT the two agent-scoped reads, which stay
# owner-or-admin so a member (or its deployed agent) can read grants for an
# agent it owns. Applied per handler rather than at router level for that
# reason -- see tinyagentos.auth_context.require_admin.
router = APIRouter()


class SecretCreate(BaseModel):
    name: str
    value: str
    category: str = "general"
    description: str = ""
    agents: list[str] = []


class SecretUpdate(BaseModel):
    value: Optional[str] = None
    category: Optional[str] = None
    description: Optional[str] = None
    agents: Optional[list[str]] = None


@router.get("/api/secrets/categories", dependencies=[Depends(require_admin)])
async def list_categories(request: Request):
    store = request.app.state.secrets
    categories = await store.get_categories()
    return categories


@router.get("/api/secrets/agent/{agent_name}")
async def get_agent_secrets(
    request: Request,
    agent_name: str,
    user: CurrentUser = Depends(current_user),
):
    """Return the secrets granted to *agent_name* -- values in PLAINTEXT.

    Owner-or-admin: the registry must attribute the agent to the caller. An
    agent the registry cannot attribute (unknown, or config-only) is readable
    by an admin only -- a non-admin never learns another agent's secrets by
    guessing its name.
    """
    await require_agent_owner_or_admin(request, user, agent_name)
    store = request.app.state.secrets
    secrets = await store.get_agent_secrets(agent_name)
    return secrets


@router.get("/api/secrets/agent/{agent_name}/github")
async def get_agent_github_grants(
    request: Request,
    agent_name: str,
    user: CurrentUser = Depends(current_user),
):
    """Return GitHub App installations granted to *agent_name*.

    Each entry includes the installation_id, repo_full_name, and
    permissions extracted from the encrypted secret value.

    Requires a valid session cookie — unauthenticated callers receive 401.
    Only the agent's owner or an admin may read its grants (403 otherwise).
    """
    registry = getattr(request.app.state, "agent_registry", None)
    if registry is not None:
        try:
            agent = await registry.get_by_handle(agent_name)
        except RuntimeError:
            agent = None
        if agent is not None:
            require_owner_or_admin(user, agent["user_id"])
    store = request.app.state.secrets
    installations = await store.get_agent_github_installations(agent_name)
    return installations


@router.get("/api/secrets/{name:path}", dependencies=[Depends(require_admin)])
async def get_secret(request: Request, name: str):
    store = request.app.state.secrets
    secret = await store.get(name)
    if not secret:
        return JSONResponse({"error": "Secret not found"}, status_code=404)
    return secret


@router.get("/api/secrets", dependencies=[Depends(require_admin)])
async def list_secrets(request: Request, category: str | None = None):
    store = request.app.state.secrets
    secrets = await store.list(category=category)
    # Mask values in list view
    for s in secrets:
        s["value"] = "***"
    return secrets


@router.post("/api/secrets", dependencies=[Depends(require_admin)])
async def add_secret(request: Request, body: SecretCreate):
    store = request.app.state.secrets
    # Check for duplicate
    existing = await store.get(body.name)
    if existing:
        return JSONResponse({"error": "Secret already exists"}, status_code=409)
    secret_id = await store.add(
        name=body.name,
        value=body.value,
        category=body.category,
        description=body.description,
        agents=body.agents,
    )
    return {"id": secret_id, "status": "created"}


@router.put("/api/secrets/{name:path}", dependencies=[Depends(require_admin)])
async def update_secret(request: Request, name: str, body: SecretUpdate):
    store = request.app.state.secrets
    updated = await store.update(
        name=name,
        value=body.value,
        category=body.category,
        description=body.description,
        agents=body.agents,
    )
    if not updated:
        return JSONResponse({"error": "Secret not found"}, status_code=404)
    return {"status": "updated"}


@router.delete("/api/secrets/{name:path}", dependencies=[Depends(require_admin)])
async def delete_secret(request: Request, name: str):
    store = request.app.state.secrets
    deleted = await store.delete(name)
    if not deleted:
        return JSONResponse({"error": "Secret not found"}, status_code=404)
    return {"status": "deleted"}
