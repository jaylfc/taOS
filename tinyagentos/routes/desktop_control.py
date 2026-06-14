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
import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
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
