"""Adds security headers (CSP, X-Frame-Options, X-Content-Type-Options) to every response."""
from __future__ import annotations

import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# The Host header is attacker-controllable; only interpolate it into the CSP
# when it is a bare hostname/IP (no spaces, ';' or other CSP-breaking chars),
# otherwise a crafted Host could inject CSP directives.
_SAFE_HOST_RE = re.compile(r"^[A-Za-z0-9.\-\[\]]+$")

# connect-src includes ws:/wss: so WebSocket upgrades are permitted.
# style-src includes 'unsafe-inline' because the server-rendered auth pages
# embed styles inline (no build step there).
def _build_csp(frame_src_extra: str = "") -> str:
    # frame-src must name the browser-proxy origin (separate port) so the
    # BrowserApp can frame proxied pages; without it default-src 'self' blocks
    # the cross-origin proxy iframe. 'self' covers single-port mode.
    frame_src = "frame-src 'self'" + (f" {frame_src_extra}" if frame_src_extra else "")
    return (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: https: blob:; "
        f"{frame_src}; "
        # data: lets the canvas (tldraw) load its bundled translation URIs
        # (data:application/json), which are inline data, not a network fetch.
        "connect-src 'self' ws: wss: data:"
    )


def _strip_port(host: str) -> str:
    # A bracketed IPv6 host ("[::1]" or "[::1]:6969") is full of colons, so a
    # naive rsplit on ":" would corrupt it. Keep everything up to the closing
    # bracket; for a normal "host:port" just drop the trailing port.
    if host.startswith("["):
        end = host.find("]")
        return host[: end + 1] if end != -1 else host
    return host.rsplit(":", 1)[0] if ":" in host else host


def _proxy_frame_origin(request: Request) -> str:
    """The browser-proxy origin (same host, proxy port) to allow in frame-src,
    or "" when single-port (proxy served from the main origin, already 'self')."""
    state = request.app.state
    main_port = getattr(state, "main_port", None)
    proxy_port = getattr(state, "browser_proxy_port", 0)
    if not main_port or not proxy_port or main_port == proxy_port:
        return ""
    host = _strip_port(request.headers.get("host") or "")
    if not host or not _SAFE_HOST_RE.fullmatch(host):
        return ""
    scheme = (request.url.scheme or "http").lower()
    scheme = scheme if scheme in ("http", "https") else "http"
    # Allow both schemes for the host:port so a scheme mismatch behind a proxy
    # terminator does not break framing.
    return f"http://{host}:{proxy_port} https://{host}:{proxy_port} {scheme}://{host}:{proxy_port}"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        csp = _build_csp(_proxy_frame_origin(request))
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        return response
