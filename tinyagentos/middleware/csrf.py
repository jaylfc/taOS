"""CSRF protection — signed double-submit cookie pattern.

How it works
------------
1. ``CSRFMiddleware`` sets a ``csrf_token`` cookie (non-HttpOnly, so JS can
   read it) on every outgoing response that does not already carry one.
   The token is ``HMAC-SHA256(server_signing_key, session_id)`` so it is
   cryptographically bound to the caller's ``taos_session`` cookie.
   Requests that carry no ``taos_session`` get a token keyed on the empty
   string (still better than an unbound random value; the exemption at
   ``verify_csrf`` means those requests are never checked anyway).
2. ``verify_csrf`` is a FastAPI dependency.  State-mutating routes
   (POST / PUT / PATCH / DELETE) that rely on session-cookie auth include
   this dependency.  It recomputes the HMAC over the request's own
   ``taos_session`` value and compares the result to the ``X-CSRF-Token``
   header using ``secrets.compare_digest``.  A token minted for session A
   will not verify under session B.
3. Routes authenticated exclusively via ``Authorization: Bearer <token>``
   do *not* need CSRF protection — the bearer token itself is unforgeable
   from a third-party origin.  Those routes skip ``verify_csrf``.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import Response

_COOKIE_NAME = "csrf_token"
_HEADER_NAME = "x-csrf-token"
_TOKEN_BYTES = 32  # 256 bits

# Routes that ESTABLISH a credential rather than act on one.
#
# These are exempt by PATH, deliberately and permanently.  They used to be
# exempt only as a side effect of the "no taos_session cookie" rule below,
# which is a proxy for "not signed in" — and that proxy inverts at exactly the
# wrong moment.  A browser holding an EXPIRED session cookie still SENDS it, so
# the rule concluded "this request is cookie-authenticated, enforce CSRF" about
# a user who was not authenticated at all and was trying to fix that.  The
# server-rendered sign-in form cannot satisfy a double-submit check either: it
# is a plain HTML form POST with no JavaScript to attach an X-CSRF-Token
# header.  The result was a 403 on every sign-in surface, i.e. a lockout that
# retrying cannot clear — terminal on a keyboard-less kiosk (#2081).
#
# Exempting them costs nothing that was ever being protected: there is no
# session to hijack until one of these routes mints it.  Login CSRF (forcing a
# victim to sign in as the attacker) remains possible, as it already was for
# every cookie-less caller, which is the overwhelmingly common case.  Closing
# that would need a signed hidden form field, not this dependency.
#
# Keep this list MINIMAL.  Anything that acts on an already-valid session must
# stay protected; `tests/test_csrf_login_lockout.py` holds that direction.
_CREDENTIAL_PATHS = frozenset(
    {
        "/auth/login",       # password form + SPA handoff
        "/auth/pin-login",   # console PIN keypad
        "/auth/setup",       # first-run account creation
        "/auth/complete",    # invited user setting their password
        "/setup/complete",   # first-boot wizard (dashboard router, form POST)
    }
)


def _get_signing_key(app) -> bytes | None:
    """Return the HMAC signing key from app state, or None if not configured."""
    state = getattr(app, "state", None)
    if state is None:
        return None
    return getattr(state, "browser_session_signing_key", None) or None


def _bind_token(session_id: str, signing_key: bytes) -> str:
    """HMAC-SHA256(signing_key, session_id) — session-bound CSRF token."""
    msg = session_id.encode() if session_id else b""
    return hmac.new(signing_key, msg, hashlib.sha256).hexdigest()


class CSRFMiddleware(BaseHTTPMiddleware):
    """Ensure every response carries a ``csrf_token`` cookie.

    The cookie is:
    * ``SameSite=Strict`` — blocks cross-site requests at the browser level
      (defence in depth; the double-submit check is the hard enforcement).
    * NOT ``HttpOnly`` — JavaScript must be able to read it so the SPA can
      include it in ``X-CSRF-Token`` headers for API calls.
    * ``Path=/`` — available site-wide.
    * No ``max_age`` — session cookie; expires on browser close.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        existing = request.cookies.get(_COOKIE_NAME)
        response = await call_next(request)
        if not existing:
            signing_key = _get_signing_key(request.app)
            session_id = request.cookies.get("taos_session", "")
            if signing_key is not None:
                token = _bind_token(session_id, signing_key)
            else:
                token = secrets.token_hex(_TOKEN_BYTES)
            response.set_cookie(
                _COOKIE_NAME,
                token,
                httponly=False,
                samesite="strict",
                path="/",
            )
        return response


def verify_csrf(conn: HTTPConnection) -> None:
    """FastAPI dependency — enforce the double-submit CSRF check.

    Typed as ``HTTPConnection`` (the shared base of ``Request`` and
    ``WebSocket``) so FastAPI injects it on BOTH http and websocket scopes.
    A plain ``Request`` param would make FastAPI call the dependency with no
    argument on a websocket route (TypeError), and ``Request | None`` is not a
    valid injectable type at all — either one breaks route registration when
    this dependency is attached at the router level (``dependencies=_csrf``)
    and that router also carries an ``@router.websocket`` route.
    """
    # WebSocket scope has no HTTP method — not CSRF-able; skip.
    method = getattr(conn, "method", None)
    if method is None or method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return

    # Bearer-authenticated requests are not subject to CSRF.
    auth_header = conn.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return

    # Signing in must work while a stale cookie is present. Checked BEFORE the
    # cookie rule, because the stale cookie is what defeats that rule.
    if conn.url.path.rstrip("/") in _CREDENTIAL_PATHS:
        return

    # No session cookie → not cookie-authenticated → no CSRF risk.
    if not conn.cookies.get("taos_session"):
        return

    cookie_token = conn.cookies.get(_COOKIE_NAME, "")
    header_token = conn.headers.get(_HEADER_NAME, "")

    if not cookie_token or not header_token:
        raise HTTPException(status_code=403, detail="CSRF token missing")

    signing_key = _get_signing_key(getattr(conn, "app", None))
    if signing_key is not None:
        session_id = conn.cookies.get("taos_session", "")
        expected = _bind_token(session_id, signing_key)
        cookie_token = expected

    if not secrets.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")
