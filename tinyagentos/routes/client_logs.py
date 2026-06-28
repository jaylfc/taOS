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
from tinyagentos.rate_limit import RateLimiter

router = APIRouter()

# Per-user token bucket: a crash loop (or one user) must not be able to flood the
# shared ring buffer and evict everyone else's recent errors. Allows a burst of
# ~30 lines (a noisy crash) then ~1/sec sustained.
_post_limiter = RateLimiter(capacity=30, refill_per_second=1.0)


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
    # Only valid writes are rate-limited (malformed requests already 400 above
    # and never reach the buffer), so a flood of real logs cannot evict others'.
    if not _post_limiter.check(user.user_id):
        return JSONResponse({"error": "rate limited, slow down"}, status_code=429)
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
