"""CSRF protection — double-submit cookie pattern.

How it works
------------
1. ``CSRFMiddleware`` sets a ``csrf_token`` cookie (non-HttpOnly, so JS can
   read it) on every outgoing response that does not already carry one.
2. ``verify_csrf`` is a FastAPI dependency.  State-mutating routes
   (POST / PUT / PATCH / DELETE) that rely on session-cookie auth include
   this dependency.  It checks that the ``X-CSRF-Token`` request header
   matches the ``csrf_token`` cookie value.
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

import secrets

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

_COOKIE_NAME = "csrf_token"
_HEADER_NAME = "x-csrf-token"
_TOKEN_BYTES = 32  # 256 bits


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
            token = secrets.token_hex(_TOKEN_BYTES)
            response.set_cookie(
                _COOKIE_NAME,
                token,
                httponly=False,
                samesite="strict",
                path="/",
            )
        return response


def verify_csrf(request: Request) -> None:
    """FastAPI dependency — enforce the double-submit CSRF check.

    Scope
    -----
    * Safe HTTP methods (GET / HEAD / OPTIONS) are always exempt.
    * Requests authenticated via ``Authorization: Bearer …`` are exempt —
      the bearer token itself is unforgeable from a third-party origin.
    * Requests without a ``taos_session`` cookie are exempt — without an
      active cookie-session there is nothing for CSRF to hijack (login and
      setup endpoints fall into this category).

    For protected requests the ``X-CSRF-Token`` header must match the
    ``csrf_token`` cookie value (double-submit pattern).
    """
    # Safe methods need no protection.
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return

    # Bearer-authenticated requests are not subject to CSRF.
    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        return

    # No session cookie → not cookie-authenticated → no CSRF risk.
    if not request.cookies.get("taos_session"):
        return

    cookie_token = request.cookies.get(_COOKIE_NAME, "")
    header_token = request.headers.get(_HEADER_NAME, "")

    if not cookie_token or not header_token:
        raise HTTPException(status_code=403, detail="CSRF token missing")

    if not secrets.compare_digest(cookie_token, header_token):
        raise HTTPException(status_code=403, detail="CSRF token mismatch")
