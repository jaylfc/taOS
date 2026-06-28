from __future__ import annotations

import json
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

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
