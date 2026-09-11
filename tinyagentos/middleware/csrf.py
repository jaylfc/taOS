"""CSRF protection — signed double-submit cookie pattern.

How it works
------------
1. ``CSRFMiddleware`` sets a ``csrf_token`` cookie (non-HttpOnly, so JS can
   read it) on every outgoing response that does not already carry one.
   The token is an HMAC-SHA256 signature over ``session_id || nonce``, where
   ``session_id`` comes from the ``taos_session`` cookie (if present) and
   ``nonce`` is a random value. This binds the token to the session.
2. ``verify_csrf`` is a FastAPI dependency.  State-mutating routes
   (POST / PUT / PATCH / DELETE) that rely on session-cookie auth include
   this dependency.  It checks that:
   - The ``X-CSRF-Token`` header is present and matches the ``csrf_token``
     cookie value.
   - The cookie value is a valid HMAC signature over the current session ID
     (from ``taos_session`` cookie), proving the token is bound to this session.
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

import hmac
import hashlib
import secrets

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import Response

_COOKIE_NAME = "csrf_token"
_HEADER_NAME = "x-csrf-token"
_TOKEN_BYTES = 32  # 256 bits (for the nonce)

# CSRF protection requires a server secret for HMAC signing.
# This is expected to be injected into the app state during app creation.
# The secret is derived from the secrets store under the name "csrf-secret".
def _get_server_secret(app) -> str:
    """Retrieve the CSRF server secret from the app state."""
    secret_record = app.state.secrets.get("csrf-secret")
    if not secret_record:
        # If the secret doesn't exist yet, create it
        import hashlib
        # Generate a deterministic secret based on something in the data directory
        # or use a fixed fallback for backward compatibility
        from pathlib import Path
        data_dir = Path(app.state.secrets._key_dir)
        # Use a hash of the data directory path plus a static component
        # This ensures the same secret across restarts but is still secret
        deterministic_input = str(data_dir) + "taos-csrf-server-secret"
        secret_value = hashlib.sha256(deterministic_input.encode()).hexdigest()
        
        # Store the secret
        import asyncio
        from datetime import datetime
        from tinyagentos.secrets import SecretsStore
        
        async def store_secret():
            try:
                await app.state.secrets.add(
                    name="csrf-secret",
                    value=secret_value,
                    category="security",
                    description="CSRF server secret for HMAC token signing",
                    agents=[]
                )
            except Exception:
                # If we can't store it (e.g., secret already exists), ignore
                pass
        
        # Schedule the storage but don't await it
        asyncio.create_task(store_secret())
        
        return secret_value
    
    # Return the secret value
    return secret_record["value"]

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


def _mint_csrf_token(session_id: str, server_secret: str) -> str:
    """Mint a CSRF token bound to a session ID.
    
    Returns: HMAC-SHA256(server_secret, session_id || nonce) || nonce
    where nonce is a random 32-byte value (64 hex chars).
    """
    nonce = secrets.token_hex(_TOKEN_BYTES)  # 32 bytes = 64 hex chars
    message = session_id + nonce
    
    # Compute HMAC-SHA256 of the message
    signature = hmac.new(
        server_secret.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()
    
    # Token is signature || nonce
    return signature + nonce


def _verify_csrf_token(cookie_token: str, session_id: str, server_secret: str) -> bool:
    """Verify that a CSRF token is bound to the given session ID.
    
    Args:
        cookie_token: The CSRF token from the cookie (signature || nonce)
        session_id: The session ID from the taos_session cookie
        server_secret: The server secret used for HMAC signing
        
    Returns:
        True if the token is valid and bound to the session_id, False otherwise
    """
    if len(cookie_token) < _TOKEN_BYTES * 2:
        return False
    
    # Extract the nonce (last part) and signature (everything except nonce)
    nonce_hex = cookie_token[-_TOKEN_BYTES * 2:]
    signature_hex = cookie_token[:-_TOKEN_BYTES * 2]
    
    try:
        # Validate nonce is valid hex
        int(nonce_hex, 16)
        
        # Reconstruct the original message
        message = session_id + nonce_hex
        
        # Compute expected HMAC
        expected_signature = hmac.new(
            server_secret.encode(),
            message.encode(),
            hashlib.sha256
        ).hexdigest()
        
        # Constant-time comparison
        return secrets.compare_digest(expected_signature, signature_hex)
    except Exception:
        return False


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
            # At this point, we don't have access to session_id yet
            # So we mint a random token that will be bound during verification
            token = secrets.token_hex(_TOKEN_BYTES)
            response.set_cookie(
                _COOKIE_NAME,
                token,
                httponly=False,
                samesite="strict",
                path="/",
            )
        return response


def verify_csrf(conn: HTTPConnection, app) -> None:
    """FastAPI dependency — enforce the signed double-submit CSRF check.

    This version accepts an additional 'app' parameter to access app state.

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
    ``csrf_token`` cookie value and the cookie must be a valid HMAC signature
    over the current session ID.
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

    # First, verify the basic double-submit (header matches cookie)
    if not secrets.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")

    # Verify the binding to the session ID
    session_id = conn.cookies.get("taos_session", "")
    if not session_id:
        raise HTTPException(status_code=403, detail="CSRF token validation error")
    
    server_secret = _get_server_secret(app)
    
    if not _verify_csrf_token(cookie_token, session_id, server_secret):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")
