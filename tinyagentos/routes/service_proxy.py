"""Reverse-proxy routes for installed services.

Every installed service gets a stable URL:
    /apps/{app_id}/         → proxied to http://{runtime_host}:{runtime_port}/

The controller looks up the current runtime location from InstalledAppsStore.
When a service migrates between hosts, update_runtime_location() is called and
the URL keeps working transparently.

WebSocket proxying: TODO (P5). HTTP-only proxy is sufficient for P5 milestone.
The WS upgrade path will be added when the desktop window widget needs it.
"""
from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import RedirectResponse, StreamingResponse
from tinyagentos.issued_cookies import TAOS_ISSUED_COOKIES

logger = logging.getLogger(__name__)

router = APIRouter()

# Hop-by-hop headers must not be forwarded between proxy and upstream/client.
# RFC 2616 §13.5.1 + Proxy-Authorization added per security best practice.
_HOP_BY_HOP = frozenset({
    "connection",
    "keep-alive",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "host",
})

# Controller-scoped credentials must never leak to the upstream service
# container. Every cookie this origin issues is stripped out of Cookie;
# everything else in the cookie header passes through unchanged. The shared set
# is imported rather than restated -- four local copies is exactly how
# csrf_token, taos_browser and taos_cs came to leak from all four proxies.
_SENSITIVE_HEADERS = frozenset({"authorization"})
_STRIPPED_COOKIES = TAOS_ISSUED_COOKIES


# Module-level HTTP client for proxying — avoids per-request connection churn
# and allows send(stream=True) with BackgroundTask cleanup.
_http_client = httpx.AsyncClient(timeout=60.0)


def _strip_taos_cookies(cookie_header: str) -> str:
    """Remove controller-owned cookies from a Cookie header, keep the rest."""
    if not cookie_header:
        return ""
    from http.cookies import SimpleCookie
    jar = SimpleCookie()
    try:
        jar.load(cookie_header)
    except Exception:
        return cookie_header
    for name in _STRIPPED_COOKIES:
        jar.pop(name, None)
    return "; ".join(f"{k}={m.value}" for k, m in jar.items())


def _filter_headers(headers: dict) -> dict:
    filtered: dict[str, str] = {}
    for k, v in headers.items():
        kl = k.lower()
        if kl in _HOP_BY_HOP or kl in _SENSITIVE_HEADERS:
            continue
        if kl == "cookie":
            stripped = _strip_taos_cookies(v)
            if not stripped:
                continue
            filtered[k] = stripped
            continue
        filtered[k] = v
    return filtered


@router.get("/apps/{app_id}", include_in_schema=False)
async def redirect_no_slash(app_id: str):
    """Redirect /apps/{app_id} → /apps/{app_id}/ so relative links work."""
    return RedirectResponse(url=f"/apps/{app_id}/", status_code=307)


@router.api_route(
    "/apps/{app_id}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
    include_in_schema=False,
)
async def service_proxy(app_id: str, path: str, request: Request):
    installed_apps = getattr(request.app.state, "installed_apps", None)
    if installed_apps is None:
        return _json_error("Service registry unavailable", 503)

    loc = await installed_apps.get_runtime_location(app_id)
    if loc is None:
        # Check if installed at all to give a better error message.
        is_installed = await installed_apps.is_installed(app_id)
        if not is_installed:
            return _json_error(f"App '{app_id}' is not installed", 404)
        return _json_error(
            f"App '{app_id}' has no runtime location recorded. "
            "Re-install or migrate the service to register a host:port.",
            503,
        )

    ui_path = (loc.get("ui_path") or "/").rstrip("/")
    upstream_path = ui_path + "/" + path if path else ui_path + "/"
    upstream = f"http://{loc['runtime_host']}:{loc['runtime_port']}{upstream_path}"
    query = request.url.query
    if query:
        upstream = f"{upstream}?{query}"

    fwd_headers = _filter_headers(dict(request.headers))

    async def _stream_body():
        async for chunk in request.stream():
            yield chunk

    try:
        req = _http_client.build_request(
            method=request.method,
            url=upstream,
            headers=fwd_headers,
            content=_stream_body(),
        )
        upstream_resp = await _http_client.send(req, stream=True, follow_redirects=False)
    except httpx.ConnectError:
        return _json_error(
            f"Cannot reach {app_id} at {loc['runtime_host']}:{loc['runtime_port']}. "
            "The service may be stopped or still starting.",
            502,
        )
    except httpx.TimeoutException:
        return _json_error(f"Upstream {app_id} timed out", 504)

    resp_headers = _filter_headers(dict(upstream_resp.headers))

    # Rewrite Location headers so redirects stay within /apps/{app_id}/.
    # - Absolute upstream URLs (http://<host>:<port>/foo)  → /apps/{app_id}/foo
    # - Root-relative redirects (/login, /user/sign_in)     → /apps/{app_id}/login
    # - Scheme-relative and relative paths pass through unchanged.
    if "location" in upstream_resp.headers:
        loc_header = upstream_resp.headers["location"]
        upstream_prefix = f"http://{loc['runtime_host']}:{loc['runtime_port']}{ui_path}"
        if loc_header.startswith(upstream_prefix):
            relative = loc_header[len(upstream_prefix):]
            resp_headers["location"] = f"/apps/{app_id}{relative}"
        elif loc_header.startswith("/") and not loc_header.startswith("//"):
            # Root-relative redirect (e.g. "/login"). From the browser's point
            # of view this lands on the controller root, not the proxied app.
            # Strip any ui_path prefix the upstream prepended, then nest under
            # the proxy namespace.
            if ui_path and ui_path != "/" and loc_header.startswith(ui_path.rstrip("/") + "/"):
                loc_header = loc_header[len(ui_path.rstrip("/")):]
            elif ui_path and ui_path != "/" and loc_header == ui_path.rstrip("/"):
                loc_header = "/"
            resp_headers["location"] = f"/apps/{app_id}{loc_header}"

    from starlette.background import BackgroundTask
    return StreamingResponse(
        upstream_resp.aiter_bytes(),
        status_code=upstream_resp.status_code,
        headers=resp_headers,
        background=BackgroundTask(upstream_resp.aclose),
    )


# ---------------------------------------------------------------------------
# WebSocket proxy – TODO (P5)
# ---------------------------------------------------------------------------

@router.websocket("/apps/{app_id}/ws/{path:path}")
async def service_proxy_ws(app_id: str, path: str, websocket: WebSocket):
    """WebSocket proxy placeholder. Full bidirectional piping is a P5 task.

    TODO (P5): use websockets.connect() to the upstream WS URL and pipe both
    directions. The HTTP-only proxy above is sufficient for P5; the desktop
    window widget and terminal features will require this.
    """
    await websocket.close(code=1001, reason="WebSocket proxy not yet implemented")


def _json_error(message: str, status_code: int):
    from fastapi.responses import JSONResponse
    return JSONResponse({"error": message}, status_code=status_code)
