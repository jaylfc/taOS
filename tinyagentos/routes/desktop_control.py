"""Controller -> browser command channel for agent-driven desktop control.

The desktop subscribes to GET /api/desktop/stream (SSE) and re-dispatches each
command as the existing `taos:open-app` / `taos:window` CustomEvents (see
desktop/src/hooks/use-desktop-command-stream.ts). Agent tools push commands via
POST /api/desktop/command, scoped to the calling user so a user only ever drives
their own desktop. This is the backend half of the agent-OS-control layer (#836);
the browser receivers already exist and are tested.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from tinyagentos.desktop_control.broker import DesktopCommand

logger = logging.getLogger(__name__)
router = APIRouter()


def _user_id(request: Request) -> str:
    # AuthMiddleware sets request.state.user_id (a string id or None). Scoping
    # the command channel by the authenticated user is SECURITY-CRITICAL here:
    # if every caller collapsed to one id, an agent acting for user A could
    # drive user B's desktop. Unauthenticated/exempt requests have no desktop to
    # drive, so the "system" fallback is inert rather than a shared channel.
    uid = getattr(request.state, "user_id", None)
    return uid if uid else "system"


class CommandIn(BaseModel):
    kind: Literal["open-app", "window"]
    payload: dict[str, Any] = Field(default_factory=dict)


@router.post("/api/desktop/command")
async def post_command(body: CommandIn, request: Request):
    """Push one command to the calling user's desktop(s). Returns how many open
    desktops received it (0 = the user has no desktop connected right now)."""
    broker = request.app.state.desktop_command_broker
    delivered = await broker.emit(
        _user_id(request),
        DesktopCommand(kind=body.kind, payload=body.payload),
    )
    return {"delivered": delivered}


# Upper bound on an uploaded screenshot payload (base64 of the PNG). A 4K-ish
# desktop PNG is well under this; the cap stops a rogue desktop from buffering
# an unbounded body into memory.
_MAX_SCREENSHOT_B64 = 24 * 1024 * 1024  # ~24 MB base64 (~18 MB image)


class ScreenshotResultIn(BaseModel):
    request_id: str
    # data URL ("data:image/png;base64,....") or bare base64 of a PNG.
    image: str = ""
    error: str = ""


@router.post("/api/desktop/screenshot")
async def take_screenshot(request: Request):
    """Capture the calling user's live desktop and return a PNG.

    Emits a `screenshot` command to every open desktop for the user; the first
    desktop to respond uploads its rasterised canvas to
    POST /api/desktop/screenshot-result, which resolves this request. 504 if no
    desktop is connected or none responds within the deadline.

    Note: DOM rasterisation cannot read cross-origin iframes (e.g. the Browser's
    proxied page) -- the desktop chrome and native apps capture fully.
    """
    broker = request.app.state.desktop_command_broker
    user_id = _user_id(request)
    request_id = uuid.uuid4().hex
    fut = broker.register_result(request_id, user_id)
    try:
        delivered = await broker.emit(
            user_id,
            DesktopCommand(kind="screenshot", payload={"request_id": request_id}),
        )
        if delivered == 0:
            return JSONResponse(
                {"error": "no desktop connected"}, status_code=409,
            )
        try:
            result = await asyncio.wait_for(fut, timeout=20.0)
        except asyncio.TimeoutError:
            return JSONResponse(
                {"error": "desktop did not respond in time"}, status_code=504,
            )
    finally:
        broker.discard_result(request_id)

    if isinstance(result, dict) and result.get("error"):
        return JSONResponse({"error": result["error"]}, status_code=502)
    data_url: str = result.get("image", "") if isinstance(result, dict) else ""
    b64 = data_url.split(",", 1)[1] if data_url.startswith("data:") else data_url
    try:
        png = base64.b64decode(b64)
    except Exception:
        return JSONResponse({"error": "invalid image payload"}, status_code=502)
    return Response(content=png, media_type="image/png")


@router.post("/api/desktop/screenshot-result")
async def screenshot_result(body: ScreenshotResultIn, request: Request):
    """A desktop uploads its captured screenshot, resolving the waiting request.

    Scoped two ways: only this user's desktops ever receive the screenshot
    command (which carries the request_id), and resolve_result re-checks that
    the calling user owns the request before resolving."""
    if len(body.image) > _MAX_SCREENSHOT_B64:
        return JSONResponse({"error": "screenshot too large"}, status_code=413)
    broker = request.app.state.desktop_command_broker
    payload = {"error": body.error} if body.error else {"image": body.image}
    resolved = broker.resolve_result(
        body.request_id, payload, user_id=_user_id(request),
    )
    return {"resolved": resolved}


@router.get("/api/desktop/stream")
async def desktop_stream(request: Request):
    """SSE stream of desktop commands for the calling user."""
    broker = request.app.state.desktop_command_broker
    user_id = _user_id(request)
    queue = await broker.subscribe(user_id)

    async def gen():
        try:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    cmd = await asyncio.wait_for(queue.get(), timeout=10.0)
                except asyncio.TimeoutError:
                    yield ":keepalive\n\n"
                    continue
                data = json.dumps({"kind": cmd.kind, "payload": cmd.payload, "ts": cmd.ts})
                yield f"data: {data}\n\n"
        finally:
            await broker.unsubscribe(user_id, queue)

    # no-cache + X-Accel-Buffering stop nginx/proxies from buffering the stream
    # (which would delay or coalesce commands the user is waiting to see act).
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )
