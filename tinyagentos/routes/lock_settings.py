"""Settings -> Lock screen: how the console lock screen opens.

``GET /api/settings/lock`` and ``PUT /api/settings/lock`` act on the SIGNED-IN
user's own record, identified by their session and nothing else -- the body
carries no username, so one user's session cannot address another's method.

Changing the method costs the CURRENT account password even though the caller
already holds a session. The weakest method, ``swipe``, means anyone holding the
device opens taOS; being able to select it with a borrowed unlocked screen would
turn a moment's access into a permanent open door. Re-auth attempts are
throttled per user so a stolen session cannot be used to brute-force the
password through this endpoint.
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.auth import (
    UNLOCK_METHODS,
    UNLOCK_METHODS_PLANNED,
    AuthStoreCorruptError,
    _PinAttemptLimiter,
    effective_unlock_method,
)

router = APIRouter()

#: Wrong-password re-auths, keyed by user id. Same escalating, self-healing
#: shape as the PIN throttle, but its own bucket: a mistyped password here must
#: not delay the owner's PIN at the lock screen, or vice versa.
_reauth_limiter = _PinAttemptLimiter()


def _session_user(request: Request) -> dict | None:
    """The signed-in user's full record, or None.

    Session-authenticated callers only. The host local token maps to the admin
    for scripts, but "the signed-in user's own lock screen" is a person's
    setting, not something a script should re-point.
    """
    if getattr(request.state, "via", None) != "session":
        return None
    user_id = getattr(request.state, "user_id", None)
    if not user_id:
        return None
    try:
        return request.app.state.auth._find_user_by_id(user_id)
    except AuthStoreCorruptError:
        return None


def _payload(auth_mgr, record: dict) -> dict:
    sole = auth_mgr.lock_screen_user()
    return {
        "unlock_method": effective_unlock_method(record),
        "has_pin": bool(record.get("pin_hash")),
        # Swipe is honoured only on a single-account install; say so up front
        # rather than letting the user pick a method that will never apply.
        "swipe_available": bool(sole and sole.get("id") == record.get("id")),
        "methods": list(UNLOCK_METHODS),
        "planned": list(UNLOCK_METHODS_PLANNED),
    }


@router.get("/api/settings/lock")
async def get_lock_settings(request: Request):
    record = _session_user(request)
    if record is None:
        return JSONResponse({"error": "sign in to see this setting"}, status_code=401)
    return JSONResponse(_payload(request.app.state.auth, record))


@router.put("/api/settings/lock")
async def put_lock_settings(request: Request):
    auth_mgr = request.app.state.auth
    record = _session_user(request)
    if record is None:
        return JSONResponse({"error": "sign in to change this setting"}, status_code=401)

    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    method = body.get("unlock_method")
    if method in UNLOCK_METHODS_PLANNED:
        return JSONResponse({"error": f"'{method}' is not available yet"}, status_code=400)
    if not isinstance(method, str) or method not in UNLOCK_METHODS:
        return JSONResponse({"error": "unknown unlock method"}, status_code=400)

    user_id = record.get("id", "")
    wait = _reauth_limiter.retry_after(user_id)
    if wait > 0:
        return JSONResponse(
            {"error": f"Too many incorrect passwords. Try again in {wait} seconds.",
             "retry_after": wait},
            status_code=429,
            headers={"Retry-After": str(wait)},
        )

    password = body.get("current_password")
    username = record.get("username", "")
    ok = False
    if isinstance(password, str) and password:
        ok, checked = auth_mgr.check_password(password, username=username)
        # check_password resolves by username OR email; insist it landed on
        # THIS session's account and that it is a real password, not an invite.
        ok = bool(ok and checked and checked.get("id") == user_id
                  and "password_hash" in checked)
    if not ok:
        _reauth_limiter.record_failure(user_id)
        return JSONResponse({"error": "incorrect password"}, status_code=403)
    _reauth_limiter.reset(user_id)

    if method == "pin" and not record.get("pin_hash"):
        return JSONResponse(
            {"error": "Set a PIN first.", "needs_pin": True}, status_code=409,
        )
    if method == "swipe":
        sole = auth_mgr.lock_screen_user()
        if not sole or sole.get("id") != user_id:
            return JSONResponse(
                {"error": "Swipe to unlock needs this device to have a single account."},
                status_code=409,
            )

    try:
        auth_mgr.set_unlock_method(username, method)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    fresh = auth_mgr._find_user_by_id(user_id) or record
    return JSONResponse({"ok": True, **_payload(auth_mgr, fresh)})
