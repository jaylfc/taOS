from __future__ import annotations

import json
import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, field_validator

from tinyagentos.auth import get_current_user

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
            html_parts.append(
                f'<div class="{cls}">'
                f'<div class="notif-title">{level_icon} {item["title"]}</div>'
                f'<div class="notif-meta">{item["message"]} &middot; {_format_ts(item["timestamp"])}</div>'
                f'</div>'
            )
        return HTMLResponse("".join(html_parts))
    return items


@router.get("/api/notifications/count", response_class=HTMLResponse)
async def notification_count(request: Request):
    store = request.app.state.notifications
    count = await store.unread_count()
    return f"<span class='notif-badge' data-count='{count}'>{count if count else ''}</span>"


@router.get("/api/notifications/archived")
async def list_archived_notifications(request: Request):
    """History view: dismissed notifications, newest first (nothing deleted)."""
    store = request.app.state.notifications
    return await store.list_archived()


@router.post("/api/notifications/{notif_id}/read")
async def mark_read(request: Request, notif_id: int):
    store = request.app.state.notifications
    await store.mark_read(notif_id)
    return {"ok": True}


@router.post("/api/notifications/{notif_id}/archive")
async def archive_notification(request: Request, notif_id: int):
    """Dismiss a notification by archiving it; it stays in the History view."""
    store = request.app.state.notifications
    await store.archive(notif_id)
    return {"ok": True}


@router.post("/api/notifications/read-all")
async def mark_all_read(request: Request):
    store = request.app.state.notifications
    await store.mark_all_read()
    return {"ok": True}


@router.post("/api/notifications/mark-all-read")
async def mark_all_read_counted(request: Request):
    store = request.app.state.notifications
    count = await store.mark_all_read()
    return {"marked": count}


@router.get("/api/notifications/prefs")
async def get_notification_prefs(request: Request):
    store = request.app.state.notifications
    return await store.get_event_prefs()


@router.put("/api/notifications/prefs/{event_type}")
async def set_notification_pref(request: Request, event_type: str):
    store = request.app.state.notifications
    if event_type not in store.EVENT_TYPES:
        return JSONResponse({"error": "unknown_event_type"}, status_code=404)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
    if not isinstance(body, dict) or "muted" not in body:
        return JSONResponse({"error": "muted required"}, status_code=400)
    muted_raw = body["muted"]
    if not isinstance(muted_raw, bool):
        return JSONResponse({"error": "muted must be a boolean"}, status_code=400)
    muted = bool(muted_raw)
    await store.set_event_muted(event_type, muted)
    return {"event_type": event_type, "muted": muted}


# ---------------------------------------------------------------------------
# OS-level PWA web-push (VAPID). Separate key + store from the Browser copilot
# push. All three routes require a session (the auth middleware also gates
# /api/*), so the public key is only handed to a logged-in user.
# ---------------------------------------------------------------------------


@router.get("/api/notifications/push/vapid-public-key")
async def get_notifications_vapid_public_key(
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> dict[str, str]:
    """Return the server's VAPID public key for PushManager.subscribe()."""
    keypair = getattr(request.app.state, "notif_vapid_keypair", None)
    if not keypair:
        return JSONResponse({"error": "web push not available"}, status_code=503)
    public_key, _ = keypair
    return {"public_key": public_key}


class _NotifSubscribeKeys(BaseModel):
    p256dh: str
    auth: str

    @field_validator("p256dh", "auth")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("key must not be empty")
        return v


class _NotifSubscription(BaseModel):
    endpoint: str
    keys: _NotifSubscribeKeys

    @field_validator("endpoint")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("endpoint must start with https://")
        return v


class NotifSubscribeRequest(BaseModel):
    subscription: _NotifSubscription


@router.post("/api/notifications/push/subscribe")
async def subscribe_notifications_push(
    request: Request,
    body: NotifSubscribeRequest,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    store = getattr(request.app.state, "notif_push_store", None)
    if store is None:
        return JSONResponse({"error": "web push not available"}, status_code=503)
    sub = body.subscription
    await store.upsert(
        user_id=user_id,
        endpoint=sub.endpoint,
        p256dh=sub.keys.p256dh,
        auth=sub.keys.auth,
    )
    return {"ok": True}


class NotifUnsubscribeRequest(BaseModel):
    endpoint: str


@router.post("/api/notifications/push/unsubscribe")
async def unsubscribe_notifications_push(
    request: Request,
    body: NotifUnsubscribeRequest,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    store = getattr(request.app.state, "notif_push_store", None)
    if store is None:
        return JSONResponse({"error": "web push not available"}, status_code=503)
    # Ownership-scoped: a user can only unsubscribe their own endpoint. Deleting
    # by endpoint alone would let any authed user drop (or probe) another user's
    # subscription. Return 404 when nothing was deleted so the endpoint cannot
    # be used as an ownership oracle.
    deleted = await store.delete_for_user(user_id, body.endpoint)
    if deleted == 0:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return {"ok": True}
