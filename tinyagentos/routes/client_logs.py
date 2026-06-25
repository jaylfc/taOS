"""Client log capture API (#106 log capture).

POST /api/client-logs lets the desktop ship a browser-side error/warning/debug
line to the controller (where the PWA has no readable console). GET is admin-only
and returns the most recent entries so a crash can be chased server-side.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.client_log_store import VALID_LEVELS

router = APIRouter()


class ClientLogIn(BaseModel):
    level: str
    message: str
    source: str = ""
    url: str = ""
    stack: str = ""


@router.post("/api/client-logs", status_code=201)
async def post_client_log(
    body: ClientLogIn, request: Request, user: CurrentUser = Depends(current_user)
):
    """Record one client-side log line for the calling user."""
    level = body.level.strip().lower()
    if level not in VALID_LEVELS:
        return JSONResponse(
            {"error": f"level must be one of {sorted(VALID_LEVELS)}"}, status_code=400
        )
    message = body.message.strip()
    if not message:
        return JSONResponse({"error": "message required"}, status_code=400)
    store = request.app.state.client_log_store
    rec = await store.create(
        user_id=user.user_id,
        level=level,
        message=message,
        source=body.source,
        url=body.url,
        stack=body.stack,
        user_agent=request.headers.get("user-agent", ""),
    )
    return rec


@router.get("/api/client-logs")
async def list_client_logs(
    request: Request,
    level: str | None = None,
    limit: int = 200,
    user: CurrentUser = Depends(current_user),
):
    """The most recent client logs, newest first. Admin only (logs may carry
    stack traces and URLs from any user's session)."""
    if not user.is_admin:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    lvl = level.strip().lower() if level else None
    if lvl and lvl not in VALID_LEVELS:
        return JSONResponse({"error": "invalid level"}, status_code=400)
    store = request.app.state.client_log_store
    items = await store.list_recent(level=lvl, limit=limit)
    return {"items": items}
