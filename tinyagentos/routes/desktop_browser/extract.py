"""BrowserApp v2 — Readability extract endpoint.

Single function `extract_readable(html_bytes, url) -> dict` runs Mozilla's
Readability algorithm (via `readability-lxml`) over the raw HTML and returns
title + text + html + word_count. Used by:

  - Reader mode UI (PR 5 Task 9) — replaces iframe with styled article view
  - Agent context (PR 6) — same extraction, fed into agent runtime as
    "page changed" tool-result events for pinned-agent shared context

The HTTP endpoint at `/api/desktop/browser/extract` does the same auth +
SSRF gate as the proxy, then performs a fresh httpx fetch (no rewriter, no
injection — we want the raw upstream HTML) and runs the extractor.

For PR 5 the endpoint duplicates a thin slice of the proxy fetch loop
(auth + SSRF + httpx GET). A future refactor can extract a shared
`_fetcher.py` helper if both paths grow more in common.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit

import httpx
from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from lxml import html as lxml_html
from readability import Document

from tinyagentos.auth import get_current_user
from tinyagentos.routes.desktop_browser import router
from tinyagentos.routes.desktop_browser.ssrf import (
    SsrfBlockedError,
    validate_url_or_raise,
)


_logger = logging.getLogger(__name__)
_FETCH_TIMEOUT = 15.0


def extract_readable(html_bytes: bytes, url: str) -> dict[str, Any]:
    """Run Readability over `html_bytes` and return title/text/html/word_count.

    Returns an empty-ish dict on parse failure; never raises.
    """
    if not html_bytes:
        return {"title": "", "text": "", "html": "", "word_count": 0}

    try:
        decoded = html_bytes.decode("utf-8", errors="replace")
        # Pass url so readability-lxml can absolutize relative href/src via
        # make_links_absolute — without it, Reader mode renders broken links.
        doc = Document(decoded, url=url)
        title = (doc.short_title() or doc.title() or "").strip()
        summary_html = doc.summary(html_partial=True)
    except Exception as e:
        _logger.info("readability extract failed: %s", e)
        return {"title": "", "text": "", "html": "", "word_count": 0}

    text = ""
    try:
        text = lxml_html.fromstring(summary_html).text_content().strip()
    except Exception:
        _logger.debug("lxml text_content failed for extracted summary; returning empty text")

    word_count = len([w for w in text.split() if w.strip()])

    return {
        "title": title,
        "text": text,
        "html": summary_html,
        "word_count": word_count,
    }


@router.get("/api/desktop/browser/extract")
async def extract_endpoint(
    profile_id: str,  # reserved for future per-profile caching; not yet used
    url: str,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    """Auth + SSRF gate + fetch + Readability extract.

    Note: profile_id is currently unused — extracts run against raw upstream
    HTML (no cookie injection) so the result is profile-independent. The
    parameter is reserved for future per-profile extract caching.
    """
    _ = profile_id
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)

    # SSRF gate
    try:
        validate_url_or_raise(url)
    except SsrfBlockedError as e:
        parsed = urlsplit(url)
        _logger.info(
            "browser extract SSRF block: scheme=%r host=%r reason=%s",
            parsed.scheme, parsed.hostname, e,
        )
        return JSONResponse({"error": "URL blocked"}, status_code=403)

    # Fetch (no rewriter / no injection — we want raw upstream HTML for extraction).
    # Walk redirects manually so we can re-check SSRF on each hop, mirroring
    # the pattern in proxy.py. follow_redirects=False prevents httpx from silently
    # following a redirect to an internal address after the initial SSRF gate passes.
    _MAX_HOPS = 5
    response: httpx.Response | None = None
    async with httpx.AsyncClient(follow_redirects=False, timeout=_FETCH_TIMEOUT) as http:
        fetch_url = url
        for _hop in range(_MAX_HOPS):
            try:
                response = await http.get(fetch_url)
            except httpx.HTTPError as e:
                _logger.info("browser extract fetch error: err=%s", e)
                return JSONResponse({"error": "fetch failed"}, status_code=502)
            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    return JSONResponse({"error": "redirect missing Location"}, status_code=502)
                fetch_url = urljoin(fetch_url, location)
                try:
                    validate_url_or_raise(fetch_url)
                except SsrfBlockedError as e:
                    parsed = urlsplit(fetch_url)
                    _logger.info(
                        "browser extract SSRF block on redirect: scheme=%r host=%r reason=%s",
                        parsed.scheme, parsed.hostname, e,
                    )
                    return JSONResponse({"error": "URL blocked"}, status_code=403)
                continue
            break
        else:
            return JSONResponse({"error": "redirect chain too long"}, status_code=502)
    assert response is not None

    content_type = response.headers.get("content-type", "")
    if "text/html" not in content_type.lower():
        return {
            "title": "",
            "text": "",
            "html": "",
            "word_count": 0,
            "note": f"non-HTML content-type: {content_type}",
        }

    return extract_readable(response.content, url)
