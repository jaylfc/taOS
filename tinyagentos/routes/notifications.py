from __future__ import annotations

import html
import json
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, field_validator

from tinyagentos.notifications import VALID_LEVELS

from tinyagentos.auth import get_current_user
from tinyagentos.agent_token_auth import check_agent_scope_for_project
from tinyagentos.rate_limit import MovingWindowLimiter
from tinyagentos.routes.auth import _require_admin

router = APIRouter()

# Per-canonical_id rate limit for agent notification posts: 10 per 10 minutes.
_AGENT_NOTIF_WINDOW_SECS = 600
_AGENT_NOTIF_MAX_PER_WINDOW = 10
_agent_notif_limiter = MovingWindowLimiter(_AGENT_NOTIF_MAX_PER_WINDOW, _AGENT_NOTIF_WINDOW_SECS)
_agent_notif_rate_hits = _agent_notif_limiter.hits


def _notif_user_id(request: Request) -> str:
    """Resolve the calling user the way the rest of the app does.

    AuthMiddleware sets ``request.state.user_id`` for BOTH the session cookie
    and the local token (``Authorization: Bearer <token>``, which it maps to
    the primary user), so browser sessions, ``taosctl`` and host scripts all
    resolve here. A cookie-only dependency such as ``get_current_user`` would
    401 every local-token caller. Same idiom as ``routes/event_stream.py`` and
    ``routes/desktop_control.py``.
    """
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    return str(uid)


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
    user_id = _notif_user_id(request)
    store = request.app.state.notifications
    items = await store.list(unread_only=unread_only, user_id=user_id)
    # Return HTML for HTMX requests, JSON otherwise
    if request.headers.get("hx-request"):
        if not items:
            return HTMLResponse("<div style='padding:0.5rem; color:var(--pico-muted-color);'>No notifications</div>")
        html_parts = []
        for item in items:
            cls = "notif-item unread" if not item["read"] else "notif-item"
            level_icon = {"warning": "&#x26A0;&#xFE0F;", "error": "&#x274C;", "info": "&#x2139;&#xFE0F;"}.get(item["level"], "")
            # title and message are agent-supplied (the reason from
            # POST /api/broker/request lands here verbatim), so escape them at the HTML sink; the
            # JSON branch below still returns the raw text. level_icon is a
            # fixed entity literal and cls is derived from a bool.
            html_parts.append(
                f'<div class="{cls}">'
                f'<div class="notif-title">{level_icon} {html.escape(item["title"])}</div>'
                f'<div class="notif-meta">{html.escape(item["message"])} &middot; {_format_ts(item["timestamp"])}</div>'
                f'</div>'
            )
        return HTMLResponse("".join(html_parts))
    return items


class CreateNotificationRequest(BaseModel):
    title: str
    message: str
    level: str = "info"
    source: str = "system"
    data: dict | None = None


@router.post("/api/notifications")
async def create_notification(request: Request, body: CreateNotificationRequest):
    """Create a notification through the internal store.

    Human path (session/local token): admin-only via _require_admin.
    Agent path (registry JWT with notifications_write grant): the route
    verifies the JWT + grant + project binding. Delivery goes through the
    same store.add path so SSE and web-push fire.
    """
    # Check for the agent bearer token first so the dual-auth contract is
    # explicit: either path is valid, but the route decides which one applies.
    agent_cid = None
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        presented = auth_header[7:].strip()
        if presented:
            try:
                agent_cid = await check_agent_scope_for_project(
                    request, "notifications_write", body.data.get("project_id") if body.data else None
                )
            except HTTPException:
                # Bad token, missing grant, or project mismatch: fall through
                # to the human path (which will 401/403 on its own merits).
                agent_cid = None

    if agent_cid is not None:
        # Agent path: rate-limit per canonical_id.
        if not _agent_notif_limiter.check(agent_cid):
            retry = _agent_notif_limiter.retry_after(agent_cid)
            return JSONResponse(
                {"error": "rate_limited", "retry_after": retry},
                status_code=429,
                headers={"Retry-After": str(max(1, int(retry)))},
            )

        # Enforce caps only on the agent path (after agent_cid is resolved).
        if len(body.title) > 120:
            raise HTTPException(status_code=422, detail="title must be at most 120 characters")
        if len(body.message) > 1000:
            raise HTTPException(status_code=422, detail="message must be at most 1000 characters")
        if body.data is not None and len(json.dumps(body.data).encode("utf-8")) > 4096:
            raise HTTPException(status_code=422, detail="data must be at most 4 KB serialized")

        # Level: agents may post info|warning only.
        if body.level not in ("info", "warning"):
            raise HTTPException(
                status_code=400,
                detail=f"agent may only post level info or warning, got {body.level!r}",
            )

        # Resolve the recipient user_id from the grant, NOT from the body.
        project_id = body.data.get("project_id") if body.data else None
        if project_id is not None:
            project = await request.app.state.project_store.get_project(project_id)
            if project is None:
                raise HTTPException(status_code=400, detail="project_id not found")
            user_id = project.get("user_id") or ""
            if not user_id:
                raise HTTPException(status_code=409, detail="project has no owner to notify")
        else:
            admins = [u for u in request.app.state.auth.list_users() if u.get("is_admin")]
            if not admins:
                raise HTTPException(status_code=409, detail="no admin to receive an OS-level notification")
            user_id = admins[0]["id"]

        # Source comes from the grant, NOT the body.
        source = f"agent:{agent_cid}"
        # Stamp from_agent on the data payload.
        data = dict(body.data) if body.data else {}
        data["from_agent"] = agent_cid

        store = request.app.state.notifications
        await store.add(
            title=body.title,
            message=body.message,
            level=body.level,
            source=source,
            data=data,
            user_id=user_id,
        )
        return {"ok": True}

    # Human path (session/local token): unchanged.
    ok, err = _require_admin(request)
    if not ok:
        return err
    if body.level not in VALID_LEVELS:
        raise HTTPException(
            status_code=400,
            detail=f"level must be one of {sorted(VALID_LEVELS)}",
        )
    store = request.app.state.notifications
    await store.add(
        title=body.title,
        message=body.message,
        level=body.level,
        source=body.source,
        data=body.data,
    )
    return {"ok": True}


@router.get("/api/notifications/count", response_class=HTMLResponse)
async def notification_count(request: Request):
    user_id = _notif_user_id(request)
    store = request.app.state.notifications
    count = await store.unread_count(user_id=user_id)
    return f"<span class='notif-badge' data-count='{count}'>{count if count else ''}</span>"


@router.get("/api/notifications/archived")
async def list_archived_notifications(request: Request):
    """History view: dismissed notifications, newest first (nothing deleted)."""
    user_id = _notif_user_id(request)
    store = request.app.state.notifications
    return await store.list_archived(user_id=user_id)


@router.post("/api/notifications/{notif_id}/read")
async def mark_read(request: Request, notif_id: int):
    user_id = _notif_user_id(request)
    store = request.app.state.notifications
    affected = await store.mark_read(notif_id, user_id=user_id)
    if affected == 0:
        raise HTTPException(status_code=404, detail="notification not found")
    return {"ok": True}


@router.post("/api/notifications/{notif_id}/archive")
async def archive_notification(request: Request, notif_id: int):
    """Dismiss a notification by archiving it; it stays in the History view."""
    user_id = _notif_user_id(request)
    store = request.app.state.notifications
    affected = await store.archive(notif_id, user_id=user_id)
    if affected == 0:
        raise HTTPException(status_code=404, detail="notification not found")
    return {"ok": True}


@router.post("/api/notifications/read-all")
async def mark_all_read(request: Request):
    user_id = _notif_user_id(request)
    store = request.app.state.notifications
    count = await store.mark_all_read(user_id=user_id)
    return {"ok": True, "marked": count}


@router.post("/api/notifications/mark-all-read")
async def mark_all_read_counted(request: Request):
    user_id = _notif_user_id(request)
    store = request.app.state.notifications
    count = await store.mark_all_read(user_id=user_id)
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
