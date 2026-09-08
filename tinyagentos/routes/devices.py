# tinyagentos/routes/devices.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from urllib.parse import urlparse

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.device_auth import current_user_or_device
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
        if v not in ("ios", "watchos", "android"):
            raise ValueError("platform must be 'ios', 'watchos', or 'android'")
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
