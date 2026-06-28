"""Agent tool for sending the user a notification.

Lets an agent surface a short, asynchronous heads-up in the user's notification
bell (e.g. "your build finished", "I hit a snag and paused"), even when the user
is not looking at the agent's chat. This is the FYI counterpart to
request_decision: notify_user informs and needs no answer; request_decision asks
and waits for one. It calls the same NotificationStore the rest of the OS uses,
so the message lands in the bell with the existing read/archive behaviour.
"""
from __future__ import annotations

from fastapi import Request

# The bell renders these severities; anything else falls back to "info" so a
# stray level can never break rendering.
VALID_LEVELS = frozenset({"info", "success", "warning", "error"})

NOTIFY_USER_TOOL = {
    "name": "notify_user",
    "description": (
        "Send the user a short notification that appears in their notification "
        "bell, for an asynchronous heads-up they should see even when not in your "
        "chat (a finished long task, a blocker you paused on, a result worth "
        "surfacing). For a question that needs an answer, use request_decision "
        "instead. Keep it brief; this is a glanceable alert, not a conversation."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "The notification body (keep it short)."},
            "title": {"type": "string", "description": "Optional short title; defaults to 'Agent update'."},
            "level": {
                "type": "string",
                "enum": sorted(VALID_LEVELS),
                "description": "Severity: info (default), success, warning, or error.",
            },
        },
        "required": ["message"],
    },
}


async def execute_notify_user(args: dict, request: Request) -> dict:
    args = args or {}
    message = args.get("message")
    if not isinstance(message, str) or not message.strip():
        return {"error": "notify_user requires a 'message' string"}
    title = args.get("title")
    if not isinstance(title, str) or not title.strip():
        title = "Agent update"
    level = args.get("level", "info")
    if level not in VALID_LEVELS:
        level = "info"

    notifs = getattr(request.app.state, "notifications", None)
    if notifs is None:
        return {"error": "notifications are not available"}
    await notifs.add(title=title, message=message, level=level, source="agent")
    return {"ok": True, "title": title, "level": level}
