"""HTTP endpoints for agent capability grant/revoke."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from tinyagentos.auth import get_current_user
from tinyagentos.routes.desktop_browser import router

_KNOWN_PERMISSIONS = {"read_dom", "navigate", "drive", "see_cookies"}


def _validate_permissions(permissions: str) -> None:
    if not permissions or not permissions.strip():
        raise ValueError("permissions must not be empty")
    tokens = [t.strip() for t in permissions.split(",")]
    if not tokens or any(not t for t in tokens):
        raise ValueError("permissions must be a non-empty comma-separated list")
    bad = [t for t in tokens if t not in _KNOWN_PERMISSIONS]
    if bad:
        raise ValueError(f"unknown permissions: {','.join(bad)}")


@router.get("/api/desktop/browser/capabilities")
async def list_capabilities_route(
    request: Request,
    profile_id: str,
    agent_id: str | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    """Returns { grants: [{agent_id, host_pattern, permissions, granted_at, expires_at}, ...] }"""
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    grants = await request.app.state.browser_store.list_capabilities(
        user_id=user_id, profile_id=profile_id, agent_id=agent_id,
    )
    return {"grants": grants}


class GrantRequest(BaseModel):
    profile_id: str
    agent_id: str
    host_pattern: str
    permissions: str
    expires_at: str | None = None


@router.post("/api/desktop/browser/capabilities")
async def grant_capability_route(
    request: Request,
    body: GrantRequest,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    try:
        _validate_permissions(body.permissions)
        if body.expires_at is not None:
            datetime.fromisoformat(body.expires_at)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    try:
        await request.app.state.browser_store.add_capability(
            user_id=user_id,
            profile_id=body.profile_id,
            agent_id=body.agent_id,
            host_pattern=body.host_pattern,
            permissions=body.permissions,
            expires_at=body.expires_at,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"granted": True}


@router.delete("/api/desktop/browser/capabilities")
async def revoke_capability_route(
    request: Request,
    profile_id: str,
    agent_id: str,
    host_pattern: str,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    await request.app.state.browser_store.revoke_capability(
        user_id=user_id,
        profile_id=profile_id,
        agent_id=agent_id,
        host_pattern=host_pattern,
    )
    return Response(status_code=204)
