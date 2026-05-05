"""HTTP endpoints for bookmark CRUD."""
from __future__ import annotations

from typing import Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from tinyagentos.auth import get_current_user
from tinyagentos.routes.desktop_browser import router


@router.get("/api/desktop/browser/bookmarks")
async def list_bookmarks_route(
    request: Request,
    profile_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    bookmarks = await request.app.state.browser_store.list_bookmarks_for_profile(
        user_id=user_id, profile_id=profile_id,
    )
    return {"bookmarks": bookmarks}


class BookmarkRequest(BaseModel):
    profile_id: str
    url: str
    title: str


@router.post("/api/desktop/browser/bookmarks")
async def add_bookmark_route(
    request: Request,
    body: BookmarkRequest,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    try:
        bookmark_id = await request.app.state.browser_store.create_bookmark(
            user_id=user_id,
            profile_id=body.profile_id,
            url=body.url,
            title=body.title,
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"bookmark_id": bookmark_id}


@router.delete("/api/desktop/browser/bookmarks/{bookmark_id}")
async def delete_bookmark_route(
    request: Request,
    bookmark_id: str,
    profile_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    await request.app.state.browser_store.delete_bookmark(
        user_id=user_id, profile_id=profile_id, bookmark_id=bookmark_id,
    )
    return Response(status_code=204)
