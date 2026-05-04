"""HTTP endpoints for browser window-state persistence.

Used by the BrowserApp frontend (PR 4 onwards) to debounce-PUT its
Zustand window state every 2 seconds, and to GET the persisted
state on app boot for restore.
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from tinyagentos.auth import get_current_user
from tinyagentos.routes.desktop_browser import router


class WindowEntry(BaseModel):
    window_id: str
    profile_id: str
    active_tab_id: str | None = None
    state: str  # opaque JSON-serialised tab list / scroll / zoom etc.


class WindowsPutBody(BaseModel):
    windows: list[WindowEntry]


@router.get("/api/desktop/browser/windows")
async def list_browser_windows(
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> dict:
    """Return persisted browser windows for the authenticated user."""
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    store = request.app.state.browser_store
    rows = await store.list_windows(user_id=user_id)
    return {"windows": rows}


@router.put("/api/desktop/browser/windows")
async def put_browser_windows(
    body: WindowsPutBody,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> dict:
    """Upsert all windows in the body. Caller manages full snapshot."""
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    store = request.app.state.browser_store
    for entry in body.windows:
        await store.upsert_window(
            user_id=user_id,
            window_id=entry.window_id,
            profile_id=entry.profile_id,
            active_tab_id=entry.active_tab_id,
            state_json=entry.state,
        )
    return {"ok": True, "count": len(body.windows)}


@router.delete("/api/desktop/browser/windows/{window_id}")
async def delete_browser_window(
    window_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> Response:
    """Remove one persisted browser window."""
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    store = request.app.state.browser_store
    await store.delete_window(user_id=user_id, window_id=window_id)
    return Response(status_code=204)
