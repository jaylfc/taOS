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
def _build_csp(frame_src_extra: str = "", connect_src_extra: str = "") -> str:
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
        # data: lets the canvas load its bundled translation URIs
        # (data:application/json), which are inline data, not a network fetch.
        # The two open-meteo origins are the geocoding (city search) and
        # forecast APIs the built-in Weather app fetches directly; without them
        # default-src 'self' silently blocks every lookup and the app looks dead.
        "connect-src 'self' ws: wss: data: "
        "https://geocoding-api.open-meteo.com https://api.open-meteo.com"
        + (f" {connect_src_extra}" if connect_src_extra else "")
    )


# Every response under these prefixes is per-user (account data, secrets
# metadata, grants, project files), so it must never be stored by a shared
# proxy cache or replayed from the browser's back/forward cache on the next
# user of the machine.
#
# Each entry MUST keep its trailing slash. That is what makes a plain
# startswith safe against prefix confusion: "/api/" cannot match a sibling
# route such as "/api-docs", and dropping the slash to "shorten" the list
# would silently widen the match to every path that merely starts with those
# letters.
_NO_STORE_PREFIXES = ("/api/", "/agent/")


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


# On a taOSmobile handset the desktop launches apps through taos-shelld on
# loopback (desktop/src/lib/mobile-shell.ts SHELL_BASE_URL). Without this exact
# origin Chromium blocks the fetch and every launch falls back to an in-page
# window. Only a mobile device gets it.
_MOBILE_SHELL_ORIGIN = "http://127.0.0.1:6973"


def _mobile_shell_origin(request: Request) -> str:
    """The taos-shelld origin to allow in connect-src on a mobile device, else "".

    The device class is read from the taOS Agent framework decision, the same
    source the config route reports to the desktop, so the CSP and the
    desktop's own "am I a phone" answer cannot disagree."""
    decision = getattr(request.app.state, "taos_agent_framework_decision", None)
    if getattr(decision, "device_class", None) == "mobile":
        return _MOBILE_SHELL_ORIGIN
    return ""


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        csp = _build_csp(_proxy_frame_origin(request), _mobile_shell_origin(request))
        response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "geolocation=(), camera=(), microphone=(), payment=(), usb=(), "
            "accelerometer=(), gyroscope=(), magnetometer=(), sync-xhr=()",
        )
        if request.url.path.startswith(_NO_STORE_PREFIXES):
            # setdefault, never a hard set: handlers that chose their own
            # policy keep it -- SSE streams ship "no-cache" so proxies do not
            # buffer them, and the browser proxy ships "public, max-age=..."
            # for its immutable assets. Static files are mounted outside
            # these prefixes and are untouched.
            response.headers.setdefault("Cache-Control", "no-store")
        return response
