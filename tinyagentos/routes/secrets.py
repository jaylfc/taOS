from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.agent_token_auth import check_agent_scope
from tinyagentos.auth_context import (
    CurrentUser,
    current_user,
    require_admin,
    require_agent_owner_or_admin,
    require_owner_or_admin,
)
from tinyagentos.secrets import WriteOnceError

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
    write_once: bool = False


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
    Only the agent's owner or an admin may read its grants (403 otherwise). An
    agent the registry cannot attribute is readable by an admin only, the same
    rule as the plaintext secrets route above.
    """
    await require_agent_owner_or_admin(request, user, agent_name)
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
        write_once=body.write_once,
    )
    return {"id": secret_id, "status": "created"}


@router.put("/api/secrets/{name:path}", dependencies=[Depends(require_admin)])
async def update_secret(request: Request, name: str, body: SecretUpdate):
    store = request.app.state.secrets
    try:
        updated = await store.update(
            name=name,
            value=body.value,
            category=body.category,
            description=body.description,
            agents=body.agents,
        )
    except WriteOnceError:
        return JSONResponse(
            {"error": "Secret is write_once; its value is pinned and cannot be changed"},
            status_code=409,
        )
    if not updated:
        return JSONResponse({"error": "Secret not found"}, status_code=404)
    return {"status": "updated"}


@router.delete("/api/secrets/{name:path}", dependencies=[Depends(require_admin)])
async def delete_secret(request: Request, name: str):
    store = request.app.state.secrets
    force = request.query_params.get("force", "").lower() in ("1", "true", "yes")
    try:
        deleted = await store.delete(name, force=force)
    except WriteOnceError:
        return JSONResponse(
            {"error": "Secret is write_once; delete it only with an explicit force=true admin query"},
            status_code=409,
        )
    if not deleted:
        return JSONResponse({"error": "Secret not found"}, status_code=404)
    return {"status": "deleted"}


@router.get("/api/agents/self/secrets/{name}")
async def get_self_secret(
    request: Request,
    name: str,
):
    """Return a secret value if it is granted to the agent named by the verified
    token AND the token carries the ``secrets_read_own`` scope.

    The agent name is resolved from the token's canonical_id -- never from the
    path parameter ``name``.  If the secret is not granted to that agent, or the
    name is unknown, a 404 is returned with an identical body (no existence
    oracle).  Missing ``secrets_read_own`` scope yields 403.
    """
    # Verify the token holds the required scope and get the canonical_id.
    cid = await check_agent_scope(request, "secrets_read_own")
    if not cid:
        raise HTTPException(status_code=401, detail="agent token required")
    # Resolve the agent name from the canonical_id via config.agents.
    config = getattr(request.app.state, "config", None)
    agent_name = None
    if config is not None:
        for agent in config.agents:
            if isinstance(agent, dict) and agent.get("registry_canonical_id") and agent.get("registry_canonical_id") == cid:
                agent_name = agent.get("name")
                break
    if agent_name is None:
        # No config agent carries registry_canonical_id == cid, so this token
        # is not bound to any config agent. Return the SAME body as an
        # ungranted name so there is no oracle on whether the caller is bound.
        raise HTTPException(status_code=404, detail="Secret not found")
    # Look up secrets granted to this agent.
    store = request.app.state.secrets
    secrets = await store.get_agent_secrets(agent_name)
    # Find the requested secret by name; return 404 if not granted or unknown.
    for s in secrets:
        if s["name"] == name:
            return {"name": s["name"], "value": s["value"]}
    raise HTTPException(status_code=404, detail="Secret not found")
