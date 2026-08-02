# tinyagentos/routes/devices.py
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from tinyagentos.auth_context import CurrentUser, current_user
from tinyagentos.device_auth import current_user_or_device

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
        if v not in ("ios", "watchos"):
            raise ValueError("platform must be 'ios' or 'watchos'")
        return v


class PushTokenIn(BaseModel):
    push_token: str = Field(max_length=_MAX_PUSH_TOKEN)


@router.post("/api/devices/register")
async def register_device(
    body: RegisterIn, request: Request, user: CurrentUser = Depends(current_user)
):
    store = request.app.state.device_store
    if len(await store.list_for_user(user.user_id)) >= _MAX_DEVICES_PER_USER:
        return JSONResponse(
            {"error": f"device limit reached ({_MAX_DEVICES_PER_USER})"},
            status_code=429,
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
    return {"items": await store.list_for_user(user.user_id)}


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
