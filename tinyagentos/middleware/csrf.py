"""CSRF protection - signed double-submit cookie pattern.

How it works
------------
1. ``CSRFMiddleware`` sets a ``csrf_token`` cookie (non-HttpOnly, so JS can
   read it) on every outgoing response that does not already carry a valid one.
   The cookie contains a random nonce and an HMAC-SHA256 signature over the
   current ``taos_session`` ID, binding it to that session.
2. ``verify_csrf`` is a FastAPI dependency.  State-mutating routes
   (POST / PUT / PATCH / DELETE) that rely on session-cookie auth include
   this dependency.  It checks that the ``X-CSRF-Token`` request header
   matches the ``csrf_token`` cookie value and that the cookie signature is
   valid for the current session ID.
3. Routes authenticated exclusively via ``Authorization: Bearer <token>``
   do *not* need CSRF protection — the bearer token itself is unforgeable
   from a third-party origin.  Those routes skip ``verify_csrf``.

Bearer-exempt logic
-------------------
If the request carries a valid ``Authorization: Bearer …`` header the
dependency returns immediately without checking the CSRF header.  This
keeps the API / script / CLI flow unaffected.

Scope
-----
Only ``/auth/*`` mutating endpoints and any other session-authenticated
write paths should use ``Depends(verify_csrf)``.  Read-only GETs and
Bearer-gated routes are left untouched.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from http.cookies import CookieError, SimpleCookie
from pathlib import Path

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import Response

_COOKIE_NAME = "csrf_token"
_HEADER_NAME = "x-csrf-token"
_SESSION_COOKIE_NAME = "taos_session"
_TOKEN_BYTES = 32  # 256 bits
_FALLBACK_SERVER_SECRET = secrets.token_bytes(32)


def _coerce_server_secret(secret: bytes | str) -> bytes:
    if isinstance(secret, str):
        return secret.encode("utf-8")
    return bytes(secret)


def _get_server_secret(app_state) -> bytes:
    state = getattr(app_state, "state", app_state)
    cached = getattr(state, "csrf_server_secret", None)
    if cached is None:
        cached = getattr(app_state, "csrf_server_secret", None)
    if cached is not None:
        return _coerce_server_secret(cached)

    data_dir = getattr(state, "data_dir", None)
    if data_dir is None:
        secrets_store = getattr(state, "secrets", None)
        data_dir = getattr(secrets_store, "_key_dir", None)
    if data_dir is None:
        server_secret = _FALLBACK_SERVER_SECRET
    else:
        from tinyagentos.secrets import _get_fernet_key

        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        master_secret = _get_fernet_key(data_dir)
        server_secret = hmac.new(
            master_secret,
            b"tinyagentos:csrf-server-secret",
            hashlib.sha256,
        ).digest()

    try:
        setattr(state, "csrf_server_secret", server_secret)
    except (AttributeError, TypeError):
        pass
    return server_secret


def _mint_csrf_token(session_id: str, server_secret: bytes | str) -> str:
    nonce = secrets.token_hex(_TOKEN_BYTES)
    nonce_bytes = bytes.fromhex(nonce)
    message = session_id.encode("utf-8") + b"\0" + nonce_bytes
    signature = hmac.new(
        _coerce_server_secret(server_secret),
        message,
        hashlib.sha256,
    ).hexdigest()
    return f"{nonce}.{signature}"


def _verify_csrf_token(
    cookie_token: str,
    session_id: str,
    server_secret: bytes | str,
) -> bool:
    parts = cookie_token.split(".")
    if len(parts) != 2:
        return False
    nonce_hex, signature_hex = parts
    if len(nonce_hex) != _TOKEN_BYTES * 2:
        return False
    if len(signature_hex) != hashlib.sha256().digest_size * 2:
        return False
    try:
        nonce = bytes.fromhex(nonce_hex)
        signature = bytes.fromhex(signature_hex)
    except ValueError:
        return False

    message = session_id.encode("utf-8") + b"\0" + nonce
    expected = hmac.new(
        _coerce_server_secret(server_secret),
        message,
        hashlib.sha256,
    ).digest()
    return secrets.compare_digest(expected, signature)


def _session_id_from_response(response: Response, fallback: str) -> str:
    session_id = fallback
    for set_cookie in response.headers.getlist("set-cookie"):
        cookie = SimpleCookie()
        try:
            cookie.load(set_cookie)
        except CookieError:
            continue
        morsel = cookie.get(_SESSION_COOKIE_NAME)
        if morsel is None:
            continue
        session_id = morsel.value
        if morsel["max-age"] == "0":
            session_id = ""
    return session_id


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

# Every entry above is also in ``auth_middleware.EXEMPT_PATHS`` -- that is the
# authoritative list of paths reachable with no credential, and a path can only
# need this exemption if it is on it.  ``test_csrf_login_lockout.py`` asserts
# the containment so the two lists cannot drift apart.


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
        server_secret = _get_server_secret(request.app.state)
        request.state.csrf_server_secret = server_secret
        session_id = request.cookies.get(_SESSION_COOKIE_NAME, "")
        response = await call_next(request)
        session_id = _session_id_from_response(response, session_id)
        cookie_token = request.cookies.get(_COOKIE_NAME, "")
        if not _verify_csrf_token(cookie_token, session_id, server_secret):
            token = _mint_csrf_token(session_id, server_secret)
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

    Scope
    -----
    * WebSocket routes are exempt: a websocket connection has no HTTP method
      and is not susceptible to form-based CSRF (its handshake is
      authenticated in-handler via the session cookie), so skip.
    * Safe HTTP methods (GET / HEAD / OPTIONS) are always exempt.
    * Requests authenticated via ``Authorization: Bearer …`` are exempt —
      the bearer token itself is unforgeable from a third-party origin.
    * Credential-establishing routes (``_CREDENTIAL_PATHS``) are exempt by
      path.  They must work for a browser that is holding a STALE session
      cookie, which is precisely when the cookie rule below stops exempting
      them.
    * Requests without a ``taos_session`` cookie are exempt — without an
      active cookie-session there is nothing for CSRF to hijack.

    For protected requests the ``X-CSRF-Token`` header must match the
    ``csrf_token`` cookie value (double-submit pattern).
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

    if not secrets.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")

    session_id = conn.cookies.get(_SESSION_COOKIE_NAME, "")
    app = getattr(conn, "app", None) or conn.scope.get("app")
    state = getattr(app, "state", None)
    server_secret = getattr(
        getattr(conn, "state", None),
        "csrf_server_secret",
        None,
    )
    if server_secret is None and state is not None:
        server_secret = _get_server_secret(state)
    if server_secret is None:
        server_secret = _FALLBACK_SERVER_SECRET

    if not _verify_csrf_token(cookie_token, session_id, server_secret):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")
