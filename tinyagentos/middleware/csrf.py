"""CSRF protection -- double-submit cookie pattern.

How it works
------------
1. ``CSRFMiddleware`` sets a ``csrf_token`` cookie (non-HttpOnly, so JS can
   read it) on every outgoing response that does not already carry one.
2. ``verify_csrf`` is a FastAPI dependency.  State-mutating routes
   (POST / PUT / PATCH / DELETE) that rely on session-cookie auth include
   this dependency.  It checks that the ``X-CSRF-Token`` request header
   matches the ``csrf_token`` cookie value.
3. Routes authenticated exclusively via ``Authorization: Bearer <token>``
   do *not* need CSRF protection -- the bearer token itself is unforgeable
   from a third-party origin.  Those routes skip ``verify_csrf``.

Stale-session handling
----------------------
If the request carries a ``taos_session`` cookie that does NOT resolve to a
live session (expired, revoked, or from a previous install), the dependency
returns without enforcing the double-submit check.  A stale cookie is not
an authenticated session, so there is nothing for CSRF to hijack.  The
``CSRFMiddleware`` clears the stale cookie in the same response so the
browser stops sending it.

Scope
-----
Only ``/auth/*`` mutating endpoints and any other session-authenticated
write paths should use ``Depends(verify_csrf)``.  Read-only GETs and
Bearer-gated routes are left untouched.
"""
from __future__ import annotations

import secrets

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import HTTPConnection
from starlette.responses import Response

_COOKIE_NAME = "csrf_token"
_HEADER_NAME = "x-csrf-token"
_TOKEN_BYTES = 32  # 256 bits


class CsrfException(HTTPException):
    """CSRF check failed.

    Using a dedicated subclass lets the app return ``{"error": ...}`` instead
    of FastAPI's default ``{"detail": ...}`` so the SPA can render a friendly
    recoverable message rather than raw JSON detail.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=403, detail=detail)


class CSRFMiddleware(BaseHTTPMiddleware):
    """Ensure every response carries a ``csrf_token`` cookie.

    The cookie is:
    * ``SameSite=Strict`` -- blocks cross-site requests at the browser level
      (defence in depth; the double-submit check is the hard enforcement).
    * NOT ``HttpOnly`` -- JavaScript must be able to read it so the SPA can
      include it in ``X-CSRF-Token`` headers for API calls.
    * ``Path=/`` -- available site-wide.
    * No ``max_age`` -- session cookie; expires on browser close.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        existing = request.cookies.get(_COOKIE_NAME)
        response = await call_next(request)
        if not existing:
            token = secrets.token_hex(_TOKEN_BYTES)
            response.set_cookie(
                _COOKIE_NAME,
                token,
                httponly=False,
                samesite="strict",
                path="/",
            )

        taos_session = request.cookies.get("taos_session")
        if taos_session:
            auth_mgr = getattr(getattr(request.app, "state", None), "auth", None)
            if auth_mgr is not None:
                user_agent = request.headers.get("user-agent", "")
                if not auth_mgr.validate_session(taos_session, user_agent=user_agent):
                    response.delete_cookie("taos_session")

        return response


def verify_csrf(conn: HTTPConnection) -> None:
    """FastAPI dependency -- enforce the double-submit CSRF check.

    Typed as ``HTTPConnection`` (the shared base of ``Request`` and
    ``WebSocket``) so FastAPI injects it on BOTH http and websocket scopes.
    A plain ``Request`` param would make FastAPI call the dependency with no
    argument on a websocket route (TypeError), and ``Request | None`` is not a
    valid injectable type at all -- either one breaks route registration when
    this dependency is attached at the router level (``dependencies=_csrf``)
    and that router also carries an ``@router.websocket`` route.

    Scope
    -----
    * WebSocket routes are exempt: a websocket connection has no HTTP method
      and is not susceptible to form-based CSRF (its handshake is
      authenticated in-handler via the session cookie), so skip.
    * Safe HTTP methods (GET / HEAD / OPTIONS) are always exempt.
    * Requests authenticated via ``Authorization: Bearer …`` are exempt --
      the bearer token itself is unforgeable from a third-party origin.
    * Requests without a ``taos_session`` cookie are exempt -- without an
      active cookie-session there is nothing for CSRF to hijack.
    * Requests whose ``taos_session`` cookie does not resolve to a live
      session are exempt -- a stale or revoked cookie is not an authenticated
      session, so there is nothing for CSRF to hijack.

    For protected requests the ``X-CSRF-Token`` header must match the
    ``csrf_token`` cookie value (double-submit pattern).
    """
    # WebSocket scope has no HTTP method -- not CSRF-able; skip.
    method = getattr(conn, "method", None)
    if method is None or method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return

    # Bearer-authenticated requests are not subject to CSRF.
    auth_header = conn.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return

    # No session cookie -> not cookie-authenticated -> no CSRF risk.
    if not conn.cookies.get("taos_session"):
        return

    # Session cookie present -- check if it resolves to a live session.
    # A stale cookie from a previous install (or expired session) must not
    # force CSRF enforcement on a browser that has no csrf_token pair.
    app = getattr(conn, "scope", {}).get("app")
    if app is not None:
        auth_mgr = getattr(getattr(app, "state", None), "auth", None)
        if auth_mgr is not None:
            token = conn.cookies.get("taos_session", "")
            user_agent = conn.headers.get("user-agent", "")
            if not auth_mgr.validate_session(token, user_agent=user_agent):
                return

    cookie_token = conn.cookies.get(_COOKIE_NAME, "")
    header_token = conn.headers.get(_HEADER_NAME, "")

    if not cookie_token or not header_token:
        raise CsrfException("CSRF token missing")

    if not secrets.compare_digest(cookie_token, header_token):
        raise CsrfException("CSRF token mismatch")
