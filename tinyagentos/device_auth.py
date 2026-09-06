from __future__ import annotations

import time

from fastapi import HTTPException, Request

from tinyagentos.auth_context import CurrentUser

# Only refresh last_seen at most once a minute per device so authenticating on
# every request does not turn auth into a per-request DB write (write
# amplification / a DoS-on-the-DB surface).
_TOUCH_INTERVAL_S = 60


def extract_bearer(request) -> str | None:
    header = request.headers.get("authorization")
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    # RFC 6750: the auth scheme name is case-insensitive.
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


async def require_device(request: Request) -> dict:
    """FastAPI dependency: authenticate the caller as a registered device by
    its scoped token. 401 if the header is missing or the token is unknown or
    revoked. Refreshes last_seen (debounced) on success."""
    token = extract_bearer(request)
    if not token:
        raise HTTPException(status_code=401, detail="device token required")
    store = request.app.state.device_store
    device = await store.get_by_token(token)
    if device is None:
        raise HTTPException(status_code=401, detail="invalid device token")
    if time.time() - device["last_seen"] > _TOUCH_INTERVAL_S:
        await store.touch(device["device_id"])
    return device


async def current_user_or_device(request: Request) -> CurrentUser:
    """Session user OR an authenticated device bearer -> CurrentUser.

    Resolution order:
      1. If request.state.user_id is set, a session was authenticated by the
         auth middleware -- return the session's CurrentUser unchanged so
         existing user-session behaviour is preserved.
      2. Otherwise, try a device bearer via require_device. The device row's
         user_id becomes the CurrentUser.user_id.

    INVARIANT (a) -- HIGH, REAL PRIVILEGE-ESCALATION HOLE: is_admin is
    hard-coded to False. The devices table has NO admin column, so we must
    never copy the user record's is_admin flag. A device paired to an ADMIN
    user would otherwise inherit admin scope, and Decisions widens on
    is_admin: list_decisions with uid=None lists EVERY user's decisions, and
    get/answer take the admin bypass (read and answer ANY user's decisions,
    including execution/delegation/app-grant gates). A device token is a
    long-lived phone/watch bearer; it must NEVER carry instance-admin
    authority. Mirrors the precedent in devices.py `_owned_or_404`:
    "NO admin bypass here, even an admin session manages only its own devices".

    INVARIANT (c) -- this dependency is per-route, attached ONLY to the
    carded routes. It does NOT populate request.state.user_id from the device
    token, so other current_user / request.state consumers (e.g.
    create_decision) remain session-only.
    """
    uid = getattr(request.state, "user_id", None)
    if uid:
        return CurrentUser(
            user_id=uid,
            is_admin=bool(getattr(request.state, "is_admin", False)),
        )
    device = await require_device(request)
    # Stash the authenticated device for routes that need device_id-scoped
    # checks (e.g. PATCH push-token keys on the device's own row). This is
    # deliberately NOT request.state.user_id (Invariant c).
    request.state._device = device
    return CurrentUser(user_id=device["user_id"], is_admin=False)
