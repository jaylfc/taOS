"""HTTP endpoints for per-site browser permission grants."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from tinyagentos.auth import get_current_user
from tinyagentos.routes.desktop_browser import router


@router.get("/api/desktop/browser/site-permissions")
async def list_site_permissions_route(
    request: Request,
    profile_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    grants = await request.app.state.browser_store.list_site_permissions(
        user_id=user_id, profile_id=profile_id,
    )
    return {"grants": grants}


class SitePermissionRequest(BaseModel):
    profile_id: str
    host_pattern: str
    permission: str
    state: str  # 'allow' or 'deny'


@router.post("/api/desktop/browser/site-permissions")
async def set_site_permission_route(
    request: Request,
    body: SitePermissionRequest,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    try:
        await request.app.state.browser_store.set_site_permission(
            user_id=user_id,
            profile_id=body.profile_id,
            host_pattern=body.host_pattern,
            permission=body.permission,
            state=body.state,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"granted": True}


@router.delete("/api/desktop/browser/site-permissions")
async def remove_site_permission_route(
    request: Request,
    profile_id: str,
    host_pattern: str,
    permission: str,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    await request.app.state.browser_store.remove_site_permission(
        user_id=user_id,
        profile_id=profile_id,
        host_pattern=host_pattern,
        permission=permission,
    )
    return Response(status_code=204)
