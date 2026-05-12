from __future__ import annotations

import html
import secrets
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from tinyagentos.errors import ErrorResponse, error_response
from tinyagentos.scope import require_scope

router = APIRouter()


def _format_ts(ts: int) -> str:
    """Format a unix timestamp as a relative or short date string."""
    delta = int(time.time()) - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    return f"{delta // 86400}d ago"


@router.get("/api/notifications")
async def list_notifications(request: Request, unread_only: bool = False):
    store = request.app.state.notifications
    items = await store.list(unread_only=unread_only)
    # Return HTML for HTMX requests, JSON otherwise
    if request.headers.get("hx-request"):
        if not items:
            return HTMLResponse("<div style='padding:0.5rem; color:var(--pico-muted-color);'>No notifications</div>")
        html_parts = []
        for item in items:
            cls = "notif-item unread" if not item["read"] else "notif-item"
            level_icon = {"warning": "&#x26A0;&#xFE0F;", "error": "&#x274C;", "info": "&#x2139;&#xFE0F;"}.get(item["level"], "")
            # Escape token-supplied fields — ui.notify (and other agent-driven
            # paths) write arbitrary strings into title/message. Without
            # escaping, an agent could inject script tags into the desktop
            # notification panel.
            safe_title = html.escape(item["title"] or "")
            safe_message = html.escape(item["message"] or "")
            html_parts.append(
                f'<div class="{cls}">'
                f'<div class="notif-title">{level_icon} {safe_title}</div>'
                f'<div class="notif-meta">{safe_message} &middot; {_format_ts(item["timestamp"])}</div>'
                f'</div>'
            )
        return HTMLResponse("".join(html_parts))
    return items


@router.get("/api/notifications/count", response_class=HTMLResponse)
async def notification_count(request: Request):
    store = request.app.state.notifications
    count = await store.unread_count()
    return f"<span class='notif-badge' data-count='{count}'>{count if count else ''}</span>"


@router.post("/api/notifications/{notif_id}/read")
async def mark_read(request: Request, notif_id: int):
    store = request.app.state.notifications
    await store.mark_read(notif_id)
    return {"ok": True}


@router.post("/api/notifications/read-all")
async def mark_all_read(request: Request):
    store = request.app.state.notifications
    await store.mark_all_read()
    return {"ok": True}


class UiNotifyRequest(BaseModel):
    title: str = Field(..., max_length=120, description="Short notification title.", examples=["Build complete"])
    body: str = Field(..., description="Body text. Plain text in Pass 1.", examples=["PR #449 merged."])
    priority: str = Field("normal", description="One of 'low', 'normal', 'high'.", examples=["normal"])
    app_origin: str | None = Field(
        None,
        description="Optional attribution shown to the user; defaults to the calling agent's name.",
        examples=["code-review-agent"],
    )
    # action_url is intentionally NOT in the Pass 1 schema — the existing
    # single-user NotificationStore has no column for it, so accepting the
    # field would silently discard caller intent. It returns alongside the
    # multi-user store migration in Pass 2.

    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "Build complete",
                "body": "PR #449 merged.",
                "priority": "normal",
            }
        }
    }


_VALID_PRIORITIES = ("low", "normal", "high")


@router.post(
    "/api/ui/notify",
    summary="Send a notification to the user (agent → UI primitive)",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid priority or body."},
        401: {"model": ErrorResponse, "description": "Endpoint requires an agent bearer token."},
        403: {"model": ErrorResponse, "description": "Token scope does not cover ui.notify."},
        422: {"model": ErrorResponse, "description": "Request validation failed."},
    },
)
@require_scope("ui.notify")
async def ui_notify(request: Request, body: UiNotifyRequest):
    """Send a notification from the calling agent to the user.

    Requires a bearer token issued via `POST /api/agents/{name}/token/issue` with
    ui.notify (or wildcard) scope. The notification lands in the user's notification
    panel with the calling agent name in the source field (`agent:<name>`) for
    audit. Returns a client-side notification_id you can log; the id does not
    correspond to a stable DB row in Pass 1 (the NotificationStore is single-user;
    multi-user routing + per-row IDs land in Pass 2).
    """
    agent_id = getattr(request.state, "agent_id", None)
    if agent_id is None:
        return error_response(
            status_code=401,
            error="auth_required",
            detail="ui.notify requires an agent bearer token; session-cookie callers are not supported.",
            fix="Issue an agent token via POST /api/agents/{name}/token/issue and pass it as `Authorization: Bearer <token>`.",
            doc_url="/docs/agents/getting-started#auth",
        )
    if body.priority not in _VALID_PRIORITIES:
        return error_response(
            status_code=400,
            error="invalid_priority",
            detail=f"priority must be one of {list(_VALID_PRIORITIES)} (got {body.priority!r}).",
            fix="Omit the field to default to 'normal'.",
            doc_url="/docs/agents/recipes/notifying-the-user",
        )
    store = request.app.state.notifications
    source_label = body.app_origin or f"agent:{agent_id}"
    if not source_label.startswith("agent:"):
        source_label = f"agent:{source_label}"
    await store.add(
        title=body.title,
        message=body.body,
        level=body.priority,
        source=source_label,
    )
    return {
        "delivered": True,
        "notification_id": f"ntf_{secrets.token_urlsafe(8)}",
    }
