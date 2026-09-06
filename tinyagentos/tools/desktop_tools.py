"""Agent tools for driving the taOS desktop (the "agent OS control" framework).

Two thin tools that let an agent open apps and arrange windows on the user's
desktop. Each just emits a command onto the per-user DesktopCommandBroker; the
controller streams it to the browser (GET /api/desktop/stream) which re-dispatches
it to the existing window receivers. See docs/desktop-control.md.

Kept deliberately small: the whole "agent can drive the OS" capability is these
two emits plus the transport. Data actions (create a project, add a task, place
an image) are separate tools that call the existing project/canvas/image routes
and show up live via those apps' own SSE — they don't need this channel.
"""
from __future__ import annotations

from fastapi import Request

from tinyagentos.desktop_control.broker import DesktopCommand

# Known desktop app ids the agent can open. The browser resolves aliases/names
# too, but listing the common ones in the schema steers the model.
KNOWN_APPS = [
    "chat", "messages", "mail", "projects", "agents", "files", "store",
    "settings", "images", "notes", "todo", "decisions", "observatory",
    "terminal", "browser", "memory", "models",
]

OPEN_APP_TOOL = {
    "name": "open_app",
    "description": (
        "Open (or focus) an app on the user's desktop so they can see it. Use "
        "this to bring an app to the foreground while you work, e.g. open the "
        "Projects app before creating a project, or the Images app before "
        "generating artwork."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
                "description": f"App to open. One of: {', '.join(KNOWN_APPS)}.",
            },
            "props": {
                "type": "object",
                "description": "Optional deep-link props for the app (e.g. a channel or project id).",
            },
        },
        "required": ["app"],
    },
}

READ_LAYOUT_TOOL = {
    "name": "read_layout",
    "description": (
        "Read the user's current desktop layout: the screen size and every open "
        "window's position, size, and state (minimized/maximized/snapped/focused). "
        "Use this to be screen-aware before arranging or moving windows, e.g. to "
        "see which apps are open and where, then place a new window in free space."
    ),
    "input_schema": {"type": "object", "properties": {}},
}

ARRANGE_WINDOWS_TOOL = {
    "name": "arrange_windows",
    "description": (
        "Arrange the user's open windows into a tidy layout. Presets: 'tile-2' "
        "and 'tile-3' tile the top 2/3 windows side by side, 'center' centers "
        "them, 'cascade' staggers them."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "preset": {
                "type": "string",
                "enum": ["tile-2", "tile-3", "center", "cascade"],
                "description": "The layout preset to apply.",
            },
        },
        "required": ["preset"],
    },
}


def _user_id(request: Request) -> str | None:
    # Drive the desktop of the authenticated caller (AuthMiddleware ->
    # request.state.user_id). The taOS agent runs in the user's session, so this
    # resolves to that user. Returns None when there is no authenticated user;
    # the caller refuses rather than emitting onto a shared bucket.
    return getattr(request.state, "user_id", None) or None


async def execute_open_app(args: dict, request: Request) -> dict:
    app = (args or {}).get("app")
    if not app or not isinstance(app, str):
        return {"error": "open_app requires an 'app' string"}
    user_id = _user_id(request)
    if not user_id:
        return {"error": "no authenticated user desktop to drive"}
    broker = request.app.state.desktop_command_broker
    payload = {"app": app}
    if isinstance((args or {}).get("props"), dict):
        payload["props"] = args["props"]
    delivered = await broker.emit(user_id, DesktopCommand(kind="open-app", payload=payload))
    return {"ok": True, "app": app, "delivered": delivered}


async def execute_arrange_windows(args: dict, request: Request) -> dict:
    preset = (args or {}).get("preset")
    if preset not in {"tile-2", "tile-3", "center", "cascade"}:
        return {"error": "arrange_windows requires a valid 'preset'"}
    user_id = _user_id(request)
    if not user_id:
        return {"error": "no authenticated user desktop to drive"}
    broker = request.app.state.desktop_command_broker
    delivered = await broker.emit(
        user_id,
        DesktopCommand(kind="window", payload={"action": "arrange", "preset": preset}),
    )
    return {"ok": True, "preset": preset, "delivered": delivered}


async def execute_read_layout(args: dict, request: Request) -> dict:
    """Round-trip the user's desktop layout: emit a `layout` command and wait for
    the desktop to report back its getLayout() (screen + window bounds). This is
    the screen-aware READ half of the window-management API; arrange/move/etc are
    the drive half. Returns {"error": ...} if no desktop is connected or it does
    not answer in time, so the agent can fall back gracefully."""
    import asyncio
    import uuid

    user_id = _user_id(request)
    if not user_id:
        return {"error": "no authenticated user desktop to read"}
    broker = request.app.state.desktop_command_broker
    request_id = uuid.uuid4().hex
    fut = broker.register_result(request_id, user_id)
    try:
        delivered = await broker.emit(
            user_id, DesktopCommand(kind="layout", payload={"request_id": request_id})
        )
        if delivered == 0:
            return {"error": "no desktop connected"}
        try:
            result = await asyncio.wait_for(fut, timeout=10.0)
        except asyncio.TimeoutError:
            return {"error": "desktop did not respond in time"}
    finally:
        broker.discard_result(request_id)
    if isinstance(result, dict) and result.get("error"):
        return {"error": result["error"]}
    return {"ok": True, "layout": result.get("layout", {}) if isinstance(result, dict) else {}}
