"""HTTP endpoints for profile CRUD."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from tinyagentos.auth import get_current_user
from tinyagentos.routes.desktop_browser import router
from tinyagentos.routes.desktop_browser.profile import (
    LastProfileError,
    ProfileNotFoundError,
    create_profile,
    delete_profile_cascade,
    ensure_default_profiles,
    rename_profile,
)


class ProfileCreateBody(BaseModel):
    name: str
    color: str | None = None


class ProfilePatchBody(BaseModel):
    name: str | None = None
    color: str | None = None


@router.get("/api/desktop/browser/profiles")
async def list_profiles_route(
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> dict:
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    store = request.app.state.browser_store
    await ensure_default_profiles(store, user_id=user_id)
    profiles = await store.list_profiles(user_id=user_id)
    return {"profiles": profiles}


@router.post("/api/desktop/browser/profiles")
async def create_profile_route(
    body: ProfileCreateBody,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    store = request.app.state.browser_store
    await ensure_default_profiles(store, user_id=user_id)
    try:
        created = await create_profile(
            store, user_id=user_id, name=body.name, color=body.color,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    return JSONResponse(created, status_code=201)


@router.patch("/api/desktop/browser/profiles/{profile_id}")
async def patch_profile_route(
    profile_id: str,
    body: ProfilePatchBody,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    store = request.app.state.browser_store
    await ensure_default_profiles(store, user_id=user_id)
    try:
        updated = await rename_profile(
            store,
            user_id=user_id,
            profile_id=profile_id,
            name=body.name,
            color=body.color,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except ProfileNotFoundError:
        return JSONResponse({"error": "profile not found"}, status_code=404)

    return updated


@router.delete("/api/desktop/browser/profiles/{profile_id}")
async def delete_profile_route(
    profile_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    browser_store = request.app.state.browser_store
    cookie_store = request.app.state.browser_cookie_store
    await ensure_default_profiles(browser_store, user_id=user_id)

    try:
        deleted = await delete_profile_cascade(
            browser_store, cookie_store,
            user_id=user_id, profile_id=profile_id,
        )
    except LastProfileError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

    if not deleted:
        return JSONResponse({"error": "profile not found"}, status_code=404)

    return Response(status_code=204)
