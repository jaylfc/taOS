"""GET /api/desktop/browser/download — streams upstream file with attachment disposition.

Auth + SSRF gate + cookie jar, matching the proxy/extract security pattern.

Redirect strategy: walk redirects manually (follow_redirects=False) and
re-validate SSRF on every hop, identical to extract.py.  This prevents the
redirect-bypass attack where an initial URL passes the SSRF check but the
redirect target is an internal host.

The redirect walk happens *before* streaming begins so that we can still
return a JSONResponse on SSRF block or redirect-chain-too-long.  Once we
have the final URL, we open a streaming connection and yield bytes directly
to the client.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlparse, urlsplit

import httpx
from fastapi import Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse

from tinyagentos.auth import get_current_user
from tinyagentos.routes.desktop_browser import push, router
from tinyagentos.routes.desktop_browser.copilot_agent_ws import (
    _BACKGROUND_TASKS,
    _log_task_exception,
)
from tinyagentos.routes.desktop_browser.cookie_jar import (
    load_jar_for_request,
    persist_response_cookies,
)
from tinyagentos.routes.desktop_browser.ssrf import (
    SsrfBlockedError,
    validate_url_or_raise,
)


_logger = logging.getLogger(__name__)

_MAX_HOPS = 5
_FETCH_TIMEOUT = 60.0  # downloads can be larger than HTML pages


def _filename_from_url(url: str) -> str:
    """Infer a filename from the URL path.  Returns "download" as a fallback."""
    try:
        path = urlsplit(url).path
        name = unquote(path.rsplit("/", 1)[-1])
        if name and "." in name:
            return name
    except Exception:
        pass
    return "download"


def _filename_from_content_disposition(cd: str) -> str:
    """Parse filename or filename* from Content-Disposition.  Returns '' on miss."""
    # Prefer RFC 5987 filename*=UTF-8''xxx
    m = re.search(r"filename\*=UTF-8''([^;\s]+)", cd, re.IGNORECASE)
    if m:
        return unquote(m.group(1))
    m = re.search(r'filename="([^"]+)"', cd, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"filename=([^;\s]+)", cd, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def _safe_filename(filename: str) -> str:
    """Strip path-traversal components and unsafe chars from a caller-supplied filename."""
    # os.path.basename removes directory components (catches "../../etc/passwd")
    base = os.path.basename(filename)
    # Strip control chars and characters that would break Content-Disposition header parsing
    base = "".join(c for c in base if c.isprintable() and c not in '"\\')
    return base or "download"


async def _walk_redirects_streaming(
    http: httpx.AsyncClient,
    url: str,
    max_hops: int = _MAX_HOPS,
) -> tuple[httpx.Response, str]:
    """Walk redirects with per-hop SSRF re-validation, returning an OPEN streaming response.

    Caller is responsible for closing the returned response (and the client).
    Raises SsrfBlockedError if a redirect target is blocked.
    Raises httpx.TooManyRedirects on too many hops.
    Raises httpx.HTTPError on fetch failure.
    """
    fetch_url = url
    for _hop in range(max_hops):
        request_obj = http.build_request("GET", fetch_url)
        response = await http.send(request_obj, stream=True)
        if response.is_redirect:
            location = response.headers.get("location", "")
            await response.aclose()
            if not location:
                raise httpx.RemoteProtocolError("redirect missing Location", request=request_obj)
            fetch_url = urljoin(fetch_url, location)
            validate_url_or_raise(fetch_url)
            continue
        # Non-redirect — return the open streaming response
        return response, fetch_url
    raise httpx.TooManyRedirects("too many redirects", request=http.build_request("GET", fetch_url))


@router.get("/api/desktop/browser/download")
async def download_endpoint(
    request: Request,
    profile_id: str,
    url: str,
    filename: str | None = None,
    window_id: str | None = None,
    tab_id: str | None = None,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    """Stream an upstream file through the proxy as a browser download.

    Gates: auth + SSRF (with per-hop redirect re-validation) + cookie jar.
    Sets Content-Disposition: attachment so the browser shows a save dialog.
    """
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    # SSRF gate on initial URL
    try:
        validate_url_or_raise(url)
    except SsrfBlockedError as e:
        parsed = urlsplit(url)
        _logger.info(
            "browser download SSRF block: scheme=%r host=%r reason=%s",
            parsed.scheme, parsed.hostname, e,
        )
        return JSONResponse({"error": "URL blocked"}, status_code=403)

    # Load cookies for the initial host; after redirects the host may differ
    # but the jar covers the profile broadly enough for common use cases.
    host = urlparse(url).hostname or ""
    cookies = await load_jar_for_request(
        request.app.state.browser_cookie_store,
        user_id=user_id, profile_id=profile_id, host=host,
    )

    # Manage the AsyncClient lifetime — don't close until the streamer finishes.
    http = httpx.AsyncClient(
        follow_redirects=False, timeout=_FETCH_TIMEOUT, cookies=cookies,
    )

    try:
        response, final_url = await _walk_redirects_streaming(http, url)
    except SsrfBlockedError as e:
        await http.aclose()
        parsed = urlsplit(url)
        _logger.info(
            "browser download SSRF block on redirect: scheme=%r host=%r reason=%s",
            parsed.scheme, parsed.hostname, e,
        )
        return JSONResponse({"error": "URL blocked"}, status_code=403)
    except httpx.TooManyRedirects:
        await http.aclose()
        return JSONResponse({"error": "redirect chain too long"}, status_code=502)
    except httpx.HTTPError as e:
        await http.aclose()
        _logger.info("browser download fetch error: err=%s", e)
        return JSONResponse({"error": "fetch failed"}, status_code=502)

    # Filename: caller-supplied > upstream Content-Disposition > URL path
    upstream_cd = response.headers.get("content-disposition", "")
    upstream_filename = _filename_from_content_disposition(upstream_cd)
    final_name = _safe_filename(
        filename or upstream_filename or _filename_from_url(final_url)
    )

    download_id = uuid.uuid4().hex[:8]
    _store = getattr(request.app.state, "browser_store", None)
    _vapid = getattr(request.app.state, "vapid_keypair", None)

    async def streamer():
        _stream_ok = False
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
            _stream_ok = True
            try:
                await persist_response_cookies(
                    request.app.state.browser_cookie_store,
                    response.cookies,
                    user_id=user_id, profile_id=profile_id,
                )
            except Exception:
                pass  # cookie persistence failure is non-fatal
        except httpx.HTTPError as exc:
            _logger.info("browser download stream error: err=%s", exc)
            # Can't send a new response once streaming has started; just stop.
        finally:
            await response.aclose()
            await http.aclose()
            if _stream_ok and _store is not None and _vapid is not None:
                try:
                    if not await _store.is_push_muted(user_id, "system", "download-finished"):
                        payload = {
                            "title": "Download finished",
                            "body": final_name,
                            "tag": f"download:{download_id}",
                            "data": {
                                "window_id": window_id or "",
                                "tab_id": tab_id or "",
                            },
                        }
                        _task = asyncio.create_task(
                            push.send(user_id, payload, store=_store, vapid=_vapid)
                        )
                        _BACKGROUND_TASKS.add(_task)
                        _task.add_done_callback(_BACKGROUND_TASKS.discard)
                        _task.add_done_callback(_log_task_exception)
                except Exception:
                    _logger.warning("download push trigger failed", exc_info=True)

    # RFC 5987 encoded filename for non-ASCII safety
    safe_quoted = quote(final_name, safe="")

    return StreamingResponse(
        streamer(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{final_name}"; filename*=UTF-8\'\'{safe_quoted}'
            ),
        },
    )
