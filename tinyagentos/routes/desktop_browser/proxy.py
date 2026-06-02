"""BrowserApp v2 — proxy endpoint (full fetch pipeline).

PR 3 replaces PR 2's 501 stub with the real orchestrator:

  1. Auth via Depends(get_current_user)
  2. Profile resolution + auto-bootstrap of Personal/Work defaults
  3. SSRF guard on the initial URL
  4. Cookie jar load (per-(user, profile, host))
  5. httpx fetch with cookies, follow_redirects=False
  6. Manual redirect walk (up to MAX_REDIRECTS), SSRF re-check at each step
  7. For text/html: lxml rewriter + injector + strict CSP header
  8. For other content: stream pass-through, content-type preserved
  9. Persist Set-Cookie back to the jar
 10. Strip Set-Cookie from response to client (cookies live server-side)

Also exposes GET /__taos/copilot.js as a static asset.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlparse, urlsplit

import httpx
from fastapi import Depends, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from tinyagentos.auth import get_current_user
from tinyagentos.routes.desktop_browser import router
from tinyagentos.routes.desktop_browser.cookie_jar import (
    load_jar_for_request,
    persist_response_cookies,
)
from tinyagentos.routes.desktop_browser.csp import proxied_response_csp
from tinyagentos.routes.desktop_browser.extract import extract_readable
from tinyagentos.routes.desktop_browser.injector import inject_into_head
from tinyagentos.routes.desktop_browser.profile import (
    ProfileNotFoundError,
    ensure_default_profiles,
    get_profile_or_404,
)
from tinyagentos.routes.desktop_browser.rewriter import rewrite_html
from tinyagentos.routes.desktop_browser.ssrf import (
    SsrfBlockedError,
    validate_url_or_raise,
)


_logger = logging.getLogger(__name__)

_MAX_REDIRECTS = 5
_FETCH_TIMEOUT = 15.0   # seconds — total deadline including all redirect hops
_HOP_TIMEOUT = 5.0      # seconds — per-operation (connect + read) limit per hop
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB hard cap

# Headers we strip from upstream responses before returning to the client.
# `content-type` is stripped here and re-set from `media_type` on the
# response. This is critical: for HTML we re-serialize the upstream body
# as UTF-8 bytes (see rewriter/injector), so carrying through the upstream
# charset (e.g. `text/html; charset=ISO-8859-1`) would mislabel UTF-8 bytes
# and the iframe would decode them as Latin-1 — the classic `Â©` mojibake.
_STRIP_RESPONSE_HEADERS = frozenset({
    "set-cookie", "set-cookie2",
    "content-security-policy", "content-security-policy-report-only",
    "x-frame-options",
    "content-length", "transfer-encoding", "content-encoding",
    "content-type",
})

# charset= token in a Content-Type header value.
_CT_CHARSET_RE = re.compile(r"charset\s*=\s*([\"']?)([^\";,\s]+)\1", re.IGNORECASE)
# <meta charset="..."> and <meta http-equiv="Content-Type" content="...charset=...">
_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_\-]+)""",
    re.IGNORECASE,
)


def _detect_charset(content_type: str, body: bytes) -> str:
    """Resolve the charset of an upstream HTML response.

    Precedence: Content-Type header charset, then a <meta charset> /
    <meta http-equiv> declaration in the first chunk of the body, else
    UTF-8. The returned label is validated against the codec registry;
    an unknown label falls back to UTF-8.
    """
    label = ""
    m = _CT_CHARSET_RE.search(content_type or "")
    if m:
        label = m.group(2).strip()
    if not label:
        # Only sniff the head of the document — meta charset must appear
        # within the first 1024 bytes per the HTML spec.
        mm = _META_CHARSET_RE.search(body[:2048])
        if mm:
            label = mm.group(1).decode("ascii", "ignore").strip()
    if not label:
        return "utf-8"
    try:
        import codecs

        codecs.lookup(label)
    except (LookupError, ValueError):
        return "utf-8"
    return label


def _shell_origin(request: Request) -> str | None:
    """Origin of the taOS shell that frames this proxied page.

    The shell runs on the main port; the proxy serves from a separate
    origin (the proxy port). For the iframe to load, the proxied page's
    ``frame-ancestors`` must name the shell origin (same host, main port).

    Returns ``scheme://host:main_port`` derived from the request host, or
    ``None`` in single-port mode (proxy served from the main origin, where
    ``frame-ancestors 'self'`` already covers it).
    """
    state = request.app.state
    main_port = getattr(state, "main_port", None)
    proxy_port = getattr(state, "browser_proxy_port", 0)
    if not main_port or main_port == proxy_port:
        return None
    host = _strip_port(request.headers.get("host") or "")
    if not host:
        return None
    # Behind a proxy, x-forwarded-proto may be a comma list ("https, http").
    # Clamp to a known scheme so a hostile value can't deform the CSP.
    scheme = (
        request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    ).split(",")[0].strip().lower()
    if scheme not in ("http", "https"):
        scheme = "http"
    return f"{scheme}://{host}:{main_port}"


def _strip_port(host_header: str) -> str:
    """Return the host without its port, handling IPv6 literals.

    ``example.com:6969`` → ``example.com``; ``[::1]:6969`` → ``[::1]``;
    bare ``[::1]`` (no port) stays intact (a naive rsplit(":") would mangle it).
    """
    host = host_header.strip()
    if not host:
        return ""
    if host.startswith("["):
        # IPv6 literal: keep through the closing bracket, drop any :port after.
        end = host.find("]")
        return host[: end + 1] if end != -1 else host
    return host.rsplit(":", 1)[0] if ":" in host else host


@router.get("/api/desktop/browser/proxy")
async def proxy_get(
    profile_id: str,
    url: str,
    request: Request,
    tab_id: str | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),
):
    """Real proxy fetch — replaces PR 2's 501 stub."""
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    # Bootstrap default profiles (idempotent — safe per-request)
    browser_store = request.app.state.browser_store
    cookie_store = request.app.state.browser_cookie_store
    await ensure_default_profiles(browser_store, user_id=user_id)

    # Profile must exist for this user
    try:
        await get_profile_or_404(
            browser_store, user_id=user_id, profile_id=profile_id,
        )
    except ProfileNotFoundError:
        return JSONResponse({"error": "profile not found"}, status_code=404)

    # Initial SSRF check
    try:
        validate_url_or_raise(url)
    except SsrfBlockedError as e:
        parsed = urlsplit(url)
        _logger.info(
            "browser proxy SSRF block: scheme=%r host=%r reason=%s",
            parsed.scheme, parsed.hostname, e,
        )
        return JSONResponse({"error": "URL blocked"}, status_code=403)

    # Walk redirects manually so we can re-check SSRF on each step.
    # The whole walk is bounded by _FETCH_TIMEOUT (total); each individual
    # hop is further bounded by _HOP_TIMEOUT so one slow server can't
    # silently eat the entire budget.
    current_url = url
    response: httpx.Response | None = None
    # Sentinel for SSRF blocks detected inside the inner coroutine.
    _ssrf_blocked_url: list[str] = []
    _too_many_redirects: list[bool] = []

    async def _fetch_with_redirects() -> httpx.Response | None:
        nonlocal current_url
        _resp: httpx.Response | None = None
        async with httpx.AsyncClient(
            follow_redirects=False, timeout=_HOP_TIMEOUT,
        ) as http:
            for hop in range(_MAX_REDIRECTS + 1):
                host = urlparse(current_url).hostname or ""

                jar = await load_jar_for_request(
                    cookie_store, user_id=user_id, profile_id=profile_id, host=host,
                )

                try:
                    _resp = await http.get(current_url, cookies=jar)
                except httpx.HTTPError as e:
                    _logger.info("browser proxy fetch error: err=%s", e)
                    return None

                # Persist any cookies set by this hop
                await persist_response_cookies(
                    cookie_store, _resp.cookies,
                    user_id=user_id, profile_id=profile_id,
                )

                if _resp.status_code in (301, 302, 303, 307, 308):
                    location = _resp.headers.get("location")
                    if not location:
                        break
                    next_url = urljoin(current_url, location)
                    try:
                        validate_url_or_raise(next_url)
                    except SsrfBlockedError as e:
                        parsed = urlsplit(next_url)
                        _logger.info(
                            "browser proxy SSRF block on redirect: scheme=%r host=%r reason=%s",
                            parsed.scheme, parsed.hostname, e,
                        )
                        _ssrf_blocked_url.append(next_url)
                        return None
                    current_url = next_url
                    continue

                # Non-redirect — done
                break
            else:
                _too_many_redirects.append(True)
                return None
        return _resp

    try:
        response = await asyncio.wait_for(_fetch_with_redirects(), timeout=_FETCH_TIMEOUT)
    except asyncio.TimeoutError:
        return JSONResponse({"error": "fetch timed out"}, status_code=504)

    if _ssrf_blocked_url:
        return JSONResponse({"error": "URL blocked"}, status_code=403)
    if _too_many_redirects:
        return JSONResponse({"error": "too many redirects"}, status_code=508)
    if response is None:
        return JSONResponse({"error": "fetch failed"}, status_code=502)

    # Build response headers — strip the dangerous + length-related ones
    out_headers: dict[str, str] = {}
    for k, v in response.headers.items():
        if k.lower() in _STRIP_RESPONSE_HEADERS:
            continue
        out_headers[k] = v

    content_type = response.headers.get("content-type", "")

    if len(response.content) > _MAX_RESPONSE_BYTES:
        _logger.info(
            "browser proxy response too large: bytes=%d limit=%d",
            len(response.content), _MAX_RESPONSE_BYTES,
        )
        return JSONResponse(
            {"error": "response too large"}, status_code=502,
        )

    if "text/html" in content_type:
        # Rewrite + inject for HTML
        proxy_prefix = (
            f"/api/desktop/browser/proxy?profile_id={quote(profile_id, safe='')}"
            f"&url="
        )

        def _proxy_url(absolute: str) -> str:
            return f"{proxy_prefix}{quote(absolute, safe='')}"

        charset = _detect_charset(content_type, response.content)
        rewritten = rewrite_html(
            response.content, base_url=str(response.url), proxy=_proxy_url,
            charset=charset,
        )

        ws_scheme = "wss" if request.url.scheme == "https" else "ws"
        ws_url = (
            f"{ws_scheme}://{request.url.netloc}/api/desktop/browser/copilot"
            f"?profile_id={quote(profile_id, safe='')}"
        )
        injected = inject_into_head(
            rewritten,
            ws_url=ws_url,
            page_base_url=str(response.url),
            profile_id=profile_id,
        )

        # Page-change broadcast for any agents pinned to this tab.
        # Non-blocking — never delay the user's page load on agent fan-out.
        if tab_id:
            # Record authoritative current URL for this tab. Use the FINAL URL
            # after redirects (`response.url`), not the requested URL — the
            # iframe is showing whatever the redirects landed on, and capability
            # checks against the wrong host would mis-authorize ops.
            final_url = str(response.url)
            request.app.state.copilot_hub.set_tab_url(
                user_id=user_id, profile_id=profile_id, tab_id=tab_id, url=final_url,
            )
            extract_text = ""
            extract_title = ""
            try:
                # Extract from RAW upstream HTML, not the rewritten body — the
                # agent's context should reference original URLs, not proxied ones.
                extract_result = extract_readable(response.content, url)
                extract_text = (extract_result.get("text") or "")[:4000]
                extract_title = extract_result.get("title", "")
            except Exception:
                # Extraction failure is non-fatal — page-changed still fires
                pass

            asyncio.create_task(
                request.app.state.copilot_hub.push_event_to_pinned(
                    user_id=user_id,
                    profile_id=profile_id,
                    tab_id=tab_id,
                    event={
                        "event": "page-changed",
                        "url": url,
                        "title": extract_title,
                        "extract": extract_text,
                        "timestamp": time.time(),
                    },
                )
            )

        out_headers["content-security-policy"] = proxied_response_csp(_shell_origin(request))
        return Response(
            content=injected,
            status_code=response.status_code,
            headers=out_headers,
            media_type="text/html; charset=utf-8",
        )

    # Non-HTML — pass through bytes verbatim
    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=out_headers,
        media_type=content_type or "application/octet-stream",
    )


# Static asset serve for the copilot script.
_COPILOT_JS = Path(__file__).parent / "copilot.js"
_SW_JS = Path(__file__).parent / "sw.js"


@router.get("/__taos/copilot.js")
async def copilot_js():
    return FileResponse(
        _COPILOT_JS,
        media_type="application/javascript",
        headers={"Cache-Control": "public, max-age=86400, immutable"},
    )


@router.get("/__taos/sw.js")
async def service_worker_js():
    return FileResponse(
        _SW_JS,
        media_type="application/javascript",
        headers={
            # Must be set so the SW can claim the root scope, not just /__taos/
            "Service-Worker-Allowed": "/",
            # Don't cache long; one hour is plenty so updates roll out fast
            "Cache-Control": "public, max-age=3600",
        },
    )
