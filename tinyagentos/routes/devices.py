# tinyagentos/routes/devices.py
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from urllib.parse import urlparse

from tinyagentos.agent_db import find_agent
from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.device_auth import current_user_or_device
from tinyagentos.routes import agent_archive
from tinyagentos.routes.agents import pause_container, restart_container, start_container, find_agent as find_agent_route
from tinyagentos.board_audit import BoardAuditLog
from tinyagentos.routes.desktop_browser.ssrf import SsrfBlockedError, validate_url_or_raise

router = APIRouter()

# Cap devices per user so a compromised or looping client cannot issue unbounded
# scoped tokens (token-issuance + storage exhaustion). A push token is at most a
# few hundred bytes; the bound is generous but keeps a single value finite.
_MAX_DEVICES_PER_USER = 50
_MAX_PUSH_TOKEN = 4096
_MAX_DISPLAY_NAME = 200


class RegisterIn(BaseModel):
    platform: str
    display_name: str = Field(default="", max_length=_MAX_DISPLAY_NAME)
    push_token: str = Field(default="", max_length=_MAX_PUSH_TOKEN)

    @field_validator("platform")
    @classmethod
    def platform_supported(cls, v: str) -> str:
        # wearos is allowed here; embedded is deliberately NOT: an embedded
        # device is paired only through the pairing Decision (owner-approved,
        # with its scopes), never by a self-service register call.
        if v not in ("ios", "watchos", "android", "wearos"):
            raise ValueError("platform must be 'ios', 'watchos', 'android', or 'wearos'")
        return v

    @model_validator(mode="after")
    def _validate_push_token_for_platform(self) -> "RegisterIn":
        if self.platform == "android" and self.push_token:
            parsed = urlparse(self.push_token)
            if parsed.scheme not in ("http", "https") or not parsed.hostname:
                raise ValueError("push_token must be a URL for android devices")
        return self


class PushTokenIn(BaseModel):
    push_token: str = Field(max_length=_MAX_PUSH_TOKEN)


@router.post("/api/devices/register")
async def register_device(
    body: RegisterIn, request: Request, user: CurrentUser = Depends(current_user)
):
    store = request.app.state.device_store
    # Wear has no push distributor and embedded has no push channel: a
    # non-empty token would be stored and later misrouted. Empty = absent.
    # (embedded cannot reach here via RegisterIn today; the check is kept so a
    # future widening of the platform list cannot reopen it.)
    if body.platform in ("embedded", "wearos") and body.push_token:
        return JSONResponse(
            {"error": f"push_token is not accepted for {body.platform} devices"},
            status_code=400,
        )
    # A blocked device (revoked + blocked) may not re-pair under a fresh token.
    # push_token is client-supplied, so a well-behaved client that re-sends the
    # same APNs token is caught; a caller sending a DIFFERENT push token slips
    # past this -- acceptable because register already requires the owner's own
    # auth, and that caller could simply unblock instead; this is
    # defense-in-depth against silent re-pair, not a hard boundary.
    if body.push_token and await store.find_blocked_by_push_token(user.user_id, body.push_token) is not None:
        return JSONResponse(
            {"error": "device is blocked; unblock it before re-pairing"},
            status_code=403,
        )
    # _MAX_DEVICES_PER_USER slot accounting: list_for_user returns rows where
    # revoked=0 OR blocked=1, so a blocked device continues to consume a slot
    # against the per-user cap until it is unblocked (at which point the
    # blocked flag clears, the row falls out of list_for_user, and the slot
    # frees). This is deliberate: a blocked device is a retained safety valve
    # that the owner can still see and unblock, so it counts against the cap.
    if len(await store.list_for_user(user.user_id)) >= _MAX_DEVICES_PER_USER:
        return JSONResponse(
            {"error": f"device limit reached ({_MAX_DEVICES_PER_USER})"},
            status_code=429,
        )
    if body.platform == "android" and body.push_token:
        try:
            validate_url_or_raise(body.push_token, allow_private=True)
        except SsrfBlockedError:
            return JSONResponse(
                {"error": "push_token URL is not allowed"},
                status_code=400,
            )
    device = await store.register(
        user_id=user.user_id,
        platform=body.platform,
        push_token=body.push_token,
        display_name=body.display_name,
    )
    return device  # includes scoped_token, the only time it is returned


@router.get("/api/devices")
async def list_devices(request: Request, user: CurrentUser = Depends(current_user)):
    store = request.app.state.device_store
    items = await store.list_for_user(user.user_id)
    # Surface a derived "live scoped token" flag so the UI can tell at a glance
    # which devices can still authenticate (revoked OR blocked => no token).
    for d in items:
        d["live_token"] = not d.get("revoked") and not d.get("blocked")
    return {"items": items}


async def _owned_or_404(store, device_id: str, user: CurrentUser):
    # Devices are strictly personal: each holds a per-device scoped token and
    # its owner's sensor grants. Unlike system Decisions, there is NO admin
    # bypass here, so even an admin session manages only its own devices through
    # these self-service routes (a compromised admin cannot hijack a user's
    # device or its grants). Admin device management, if ever needed, is a
    # separate surface.
    device = await store.get(device_id)
    if device is None or device["revoked"] or device["user_id"] != user.user_id:
        return None
    return device


async def _owned_any_state(store, device_id: str, user: CurrentUser):
    # Ownership check that ignores the revoked/blocked flags. Used by the
    # block/unblock actions, which must operate on a device whose token is
    # already dead (blocked implies revoked). _owned_or_404 would refuse such a
    # row, making it impossible to unblock.
    device = await store.get(device_id)
    if device is None or device["user_id"] != user.user_id:
        return None
    return device


@router.patch("/api/devices/{device_id}/push-token")
async def update_push_token(
    device_id: str, body: PushTokenIn, request: Request,
    user: CurrentUser = Depends(current_user_or_device),
):
    store = request.app.state.device_store
    # A device bearer was resolved by current_user_or_device (Invariant c: the
    # middleware does not set request.state.user_id for device bearers, so
    # `user` is the synthesized non-admin CurrentUser). When present, the path
    # device_id must be THIS device's own id -- a sibling device of the same
    # user may not hijack or DoS another sibling's APNs token (Invariant b).
    device = getattr(request.state, "_device", None)
    if device is not None:
        if device["device_id"] != device_id:
            return JSONResponse({"error": "not found"}, status_code=404)
    else:
        # Session path: unchanged ownership check.
        if await _owned_or_404(store, device_id, user) is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        device = await store.get(device_id)
    if device and device.get("platform") == "android" and body.push_token:
        parsed = urlparse(body.push_token)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return JSONResponse(
                {"error": "push_token must be a URL for android devices"},
                status_code=422,
            )
        try:
            validate_url_or_raise(body.push_token, allow_private=True)
        except SsrfBlockedError:
            return JSONResponse(
                {"error": "push_token URL is not allowed"},
                status_code=400,
            )
    updated = await store.update_push_token(device_id, body.push_token)
    updated.pop("scoped_token", None)
    return updated


@router.delete("/api/devices/{device_id}")
async def revoke_device(
    device_id: str, request: Request, user: CurrentUser = Depends(current_user)
):
    store = request.app.state.device_store
    if await _owned_or_404(store, device_id, user) is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    await store.revoke(device_id)
    return {"revoked": True}


@router.post("/api/devices/{device_id}/block")
async def block_device(
    device_id: str, request: Request, user: CurrentUser = Depends(current_user)
):
    store = request.app.state.device_store
    device = await _owned_any_state(store, device_id, user)
    if device is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    changed = await store.block(device_id)
    return {"blocked": True, "changed": changed}


@router.post("/api/devices/{device_id}/unblock")
async def unblock_device(
    device_id: str, request: Request, user: CurrentUser = Depends(current_user)
):
    store = request.app.state.device_store
    device = await _owned_any_state(store, device_id, user)
    if device is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    changed = await store.unblock(device_id)
    return {"unblocked": True, "changed": changed}


# ── device-v1 agent control ──────────────────────────────────────────────────

_pause_restart_rate_limits: dict[str, float] = {}


def _check_restart_rate_limit(agent_name: str, device_id: str) -> JSONResponse | None:
    """Check rate limit for restart (1/min per agent). Returns 429 response or None."""
    global _pause_restart_rate_limits
    now = time.time()
    key = f"device:{device_id}:agent:{agent_name}"
    last = _pause_restart_rate_limits.get(key, 0)
    if now - last < 60:
        retry_after = int(60 - (now - last)) + 1
        return JSONResponse(
            {"error": "rate_limited", "retry_after": retry_after},
            status_code=429,
        )
    _pause_restart_rate_limits[key] = now
    return None


def _audit_device_action(device_id: str, action: str, detail: dict | None = None) -> None:
    """Write an audit record for a device agent action."""
    audit = getattr(request.app.state, "board_audit", None)
    if audit is not None:
        try:
            asyncio.create_task(
                audit.record(
                    task_id=device_id,
                    event=f"agent_{action}",
                    actor=f"device:{device_id}",
                    detail=detail or {},
                )
            )
        except Exception:
            pass


@router.post("/api/device/v1/agents/{name}/pause")
async def device_agent_pause(
    request: Request, name: str, user: CurrentUser = Depends(current_user_or_device)
):
    """Pause an agent on the device.

    Scope: agents:control. The agent must belong to the device owner.
    """
    device = getattr(request.state, "_device", None)
    if device is None:
        return JSONResponse({"error": "device bearer required"}, status_code=401)
    device_id = device["device_id"]

    agent = find_agent(request.app.state.config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    # The agent must belong to the device owner.
    if agent.get("user_id") != user.user_id:
        return JSONResponse({"error": f"Agent '{name}' does not belong to device owner"}, status_code=404)

    # Rate limit restart is handled separately; pause/resume have no per-agent limit.
    container_name = f"taos-agent-{name}"
    result = await pause_container(container_name)
    if not result.get("success"):
        return JSONResponse(
            {
                "error": f"Could not pause agent '{name}': {result.get('output', '').strip()}",
                "paused": False,
            },
            status_code=500,
        )

    _audit_device_action(device_id, "pause", {"agent": name})
    return {"status": "paused", "name": name, "paused": True}


@router.post("/api/device/v1/agents/{name}/resume")
async def device_agent_resume(
    request: Request, name: str, user: CurrentUser = Depends(current_user_or_device)
):
    """Resume an agent on the device.

    Scope: agents:control. The agent must belong to the device owner.
    """
    device = getattr(request.state, "_device", None)
    if device is None:
        return JSONResponse({"error": "device bearer required"}, status_code=401)
    device_id = device["device_id"]

    agent = find_agent(request.app.state.config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    # The agent must belong to the device owner.
    if agent.get("user_id") != user.user_id:
        return JSONResponse({"error": f"Agent '{name}' does not belong to device owner"}, status_code=404)

    container_name = f"taos-agent-{name}"
    result = await start_container(container_name)
    if not result.get("success"):
        return JSONResponse(
            {
                "error": f"Could not resume agent '{name}': {result.get('output', '').strip()}",
                "paused": True,
            },
            status_code=500,
        )

    _audit_device_action(device_id, "resume", {"agent": name})
    return {"status": "resumed", "name": name, "paused": False}


@router.post("/api/device/v1/agents/{name}/restart")
async def device_agent_restart(
    request: Request, name: str, user: CurrentUser = Depends(current_user_or_device)
):
    """Restart an agent on the device.

    Scope: agents:control. The agent must belong to the device owner.
    Rate-limited to 1/min per agent (429 {\"error\":\"rate_limited\",\"retry_after\":N}).
    """
    device = getattr(request.state, "_device", None)
    if device is None:
        return JSONResponse({"error": "device bearer required"}, status_code=401)
    device_id = device["device_id"]

    rate_limit_response = _check_restart_rate_limit(name, device_id)
    if rate_limit_response:
        return rate_limit_response

    agent = find_agent(request.app.state.config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    # The agent must belong to the device owner.
    if agent.get("user_id") != user.user_id:
        return JSONResponse({"error": f"Agent '{name}' does not belong to device owner"}, status_code=404)

    container_name = f"taos-agent-{name}"
    result = await restart_container(container_name)
    if not result.get("success"):
        return JSONResponse(
            {
                "error": f"Could not restart agent '{name}': {result.get('output', '').strip()}",
            },
            status_code=500,
        )

    _audit_device_action(device_id, "restart", {"agent": name})
    return {"status": "restarted", "name": name}


@router.post("/api/device/v1/agents/{name}/messages")
async def device_agent_send_message(
    request: Request, name: str, user: CurrentUser = Depends(current_user_or_device)
):
    """Send a message to an agent on the device.

    Scope: chat:send + ownership. Body {text} (1..2000 chars, 422 otherwise).
    The server resolves the owner's DM channel with that agent and posts
    through the same service call POST /api/chat/messages uses.
    404 {\"error\":\"agent_not_found\"} when the agent or its DM channel does not exist.
    The device never supplies a channel_id.
    """
    device = getattr(request.state, "_device", None)
    if device is None:
        return JSONResponse({"error": "device bearer required"}, status_code=401)
    device_id = device["device_id"]

    agent = find_agent(request.app.state.config, name)
    if not agent:
        return JSONResponse({"error": "agent_not_found"}, status_code=404)
    # The agent must belong to the device owner.
    if agent.get("user_id") != user.user_id:
        return JSONResponse({"error": "agent_not_found"}, status_code=404)

    body = await request.json()
    text = body.get("text", "")
    if not isinstance(text, str) or len(text) < 1 or len(text) > 2000:
        return JSONResponse({"error": "text must be 1-2000 characters"}, status_code=422)

    # Resolve the DM channel for this agent. The channel name is the agent name.
    ch_store = request.app.state.chat_channels
    channel = await ch_store.get_channel(name)
    if not channel:
        return JSONResponse({"error": "agent_not_found"}, status_code=404)

    # Post message through the same service call as POST /api/chat/messages
    msg_store = request.app.state.chat_messages
    ch_store_local = request.app.state.chat_channels
    hub = request.app.state.chat_hub

    content = text
    _http_channel = await ch_store_local.get_channel(name)
    _http_ttl = None
    if _http_channel and _http_channel.get("settings"):
        _http_ttl = _http_channel["settings"].get("ephemeral_ttl_seconds")
    import time as _time
    _http_expires_at = (_time.time() + _http_ttl) if isinstance(_http_ttl, (int, float)) and _http_ttl > 0 else None

    message = await msg_store.send_message(
        channel_id=name,
        author_id=user.user_id,
        author_type="user",
        content=content,
        content_type="text",
        thread_id=None,
        embeds=None,
        components=None,
        attachments=[],
        content_blocks=None,
        metadata=None,
        state="complete",
        expires_at=_http_expires_at,
    )
    await ch_store_local.update_last_message_at(name)
    await hub.broadcast(name, {"type": "message", "seq": hub.next_seq(), **message})

    _audit_device_action(device_id, "message", {"agent": name, "text_length": len(text)})
    return message
