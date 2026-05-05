"""HTTP endpoints for Web Push subscription CRUD and mute management."""
from __future__ import annotations

from typing import Any, Literal

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

from tinyagentos.auth import get_current_user
from tinyagentos.routes.desktop_browser import router


# ---------------------------------------------------------------------------
# Public endpoint — NO auth required.
# ---------------------------------------------------------------------------


@router.get("/api/desktop/browser/push/vapid-public-key")
async def get_vapid_public_key(request: Request) -> dict[str, str]:
    """Return the server's VAPID public key for use in PushManager.subscribe()."""
    public_key, _ = request.app.state.vapid_keypair
    return {"public_key": public_key}


# ---------------------------------------------------------------------------
# POST /subscribe
# ---------------------------------------------------------------------------


class SubscribeRequest(BaseModel):
    device_id: str
    endpoint: str
    p256dh_key: str
    auth_key: str
    user_agent: str | None = None

    @field_validator("device_id", "endpoint", "p256dh_key", "auth_key")
    @classmethod
    def must_be_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("field must not be empty")
        return v

    @field_validator("endpoint")
    @classmethod
    def endpoint_must_be_https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("endpoint must start with https://")
        return v


@router.post("/api/desktop/browser/push/subscribe")
async def subscribe_push(
    request: Request,
    body: SubscribeRequest,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    await request.app.state.browser_store.upsert_push_subscription(
        user_id=user_id,
        device_id=body.device_id,
        endpoint=body.endpoint,
        p256dh_key=body.p256dh_key,
        auth_key=body.auth_key,
        user_agent=body.user_agent,
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# GET /subscriptions
# ---------------------------------------------------------------------------


@router.get("/api/desktop/browser/push/subscriptions")
async def list_push_subscriptions(
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    rows = await request.app.state.browser_store.list_push_subscriptions(user_id=user_id)
    # Strip p256dh_key and auth_key — they are E2E encryption secrets.
    safe = [
        {
            "device_id": r["device_id"],
            "endpoint": r["endpoint"],
            "user_agent": r["user_agent"],
            "created_at": r["created_at"],
            "last_seen_at": r["last_seen_at"],
        }
        for r in rows
    ]
    return {"subscriptions": safe}


# ---------------------------------------------------------------------------
# DELETE /subscriptions/{device_id}
# ---------------------------------------------------------------------------


@router.delete("/api/desktop/browser/push/subscriptions/{device_id}")
async def delete_push_subscription(
    request: Request,
    device_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    deleted = await request.app.state.browser_store.delete_push_subscription(
        user_id=user_id, device_id=device_id,
    )
    return {"ok": deleted}


# ---------------------------------------------------------------------------
# GET /mutes
# ---------------------------------------------------------------------------


@router.get("/api/desktop/browser/push/mutes")
async def list_push_mutes(
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    mutes = await request.app.state.browser_store.list_push_mutes(user_id)
    return {"mutes": mutes}


# ---------------------------------------------------------------------------
# PUT /mutes
# ---------------------------------------------------------------------------


class SetMuteRequest(BaseModel):
    agent_id: str
    kind: Literal["chat", "drive-started", "download-finished"]
    muted: bool


@router.put("/api/desktop/browser/push/mutes")
async def set_push_mute(
    request: Request,
    body: SetMuteRequest,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    await request.app.state.browser_store.set_push_mute(
        user_id, body.agent_id, body.kind, body.muted
    )
    return {"ok": True}
