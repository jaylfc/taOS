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
    "chat", "messages", "projects", "agents", "files", "store", "settings",
    "images", "terminal", "browser", "memory", "models",
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


def _user_id(request: Request) -> str:
    # Drive the desktop of the authenticated caller (AuthMiddleware ->
    # request.state.user_id). The taOS agent runs in the user's session, so this
    # resolves to that user; a caller with no session has no desktop to drive.
    uid = getattr(request.state, "user_id", None)
    return uid if uid else "system"


async def execute_open_app(args: dict, request: Request) -> dict:
    app = (args or {}).get("app")
    if not app or not isinstance(app, str):
        return {"error": "open_app requires an 'app' string"}
    broker = request.app.state.desktop_command_broker
    payload = {"app": app}
    if isinstance((args or {}).get("props"), dict):
        payload["props"] = args["props"]
    delivered = await broker.emit(_user_id(request), DesktopCommand(kind="open-app", payload=payload))
    return {"ok": True, "app": app, "delivered": delivered}


async def execute_arrange_windows(args: dict, request: Request) -> dict:
    preset = (args or {}).get("preset")
    if preset not in {"tile-2", "tile-3", "center", "cascade"}:
        return {"error": "arrange_windows requires a valid 'preset'"}
    broker = request.app.state.desktop_command_broker
    delivered = await broker.emit(
        _user_id(request),
        DesktopCommand(kind="window", payload={"action": "arrange", "preset": preset}),
    )
    return {"ok": True, "preset": preset, "delivered": delivered}
