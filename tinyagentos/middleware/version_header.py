"""Adds X-Taos-Version to every response so the frontend can detect
backend version changes via opportunistic header sniffing rather than
a dedicated /api/version request."""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

import tinyagentos


class VersionHeaderMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Taos-Version"] = tinyagentos.__version__
        return response
