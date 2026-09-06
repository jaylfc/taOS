"""Adds X-Taos-Version to every response so the frontend can detect
backend version changes via opportunistic header sniffing rather than
a dedicated /api/version request."""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

import tinyagentos

# Unauthenticated callers (exempt paths, loopback shutdown) receive a
# coarsened value -- enough for the SPA to know a backend is alive, but not
# the exact build fingerprint an attacker could use for targeted CVE scanning.
# The full version is only revealed to callers that presented a credential
# (session, local token, registry JWT, or device bearer).
_COARSENED_VERSION = "taOS"


class VersionHeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        via = getattr(request.state, "via", None)
        if via in (None, "exempt", "loopback"):
            response.headers["X-Taos-Version"] = _COARSENED_VERSION
        else:
            response.headers["X-Taos-Version"] = tinyagentos.__version__
        return response
