"""Lightweight URL preview fetcher.

Returns OG/twitter card / HTML title metadata. Never raises:
on any failure (timeout, non-2xx, parse error) returns a fallback
dict with title=url and empty description.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0
# Cap unfurl response bodies. Pages over this limit are truncated; the
# parser only needs <head> metadata so a megabyte is generous.
_MAX_BODY_BYTES = 1_000_000


async def _check_ssrf_safe(host: str) -> None:
    """Resolve host and reject private / loopback / link-local addresses."""
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, None)
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        ):
            raise ValueError(f"refusing to fetch non-public address: {ip}")


async def _http_get(url: str) -> tuple[int, str]:
    """Indirection seam for tests. Production path enforces SSRF + size cap."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"unsupported scheme: {parsed.scheme!r}")
    if not parsed.hostname:
        raise ValueError("missing host")
    await _check_ssrf_safe(parsed.hostname)
    # follow_redirects=False so a redirect to localhost/private cannot bypass
    # the resolution check above.
    async with httpx.AsyncClient(
        follow_redirects=False,
        timeout=_TIMEOUT,
        headers={"User-Agent": "taOS-canvas-unfurl/0.1"},
    ) as client:
        async with client.stream("GET", url) as r:
            chunks: list[bytes] = []
            total = 0
            async for chunk in r.aiter_bytes():
                total += len(chunk)
                chunks.append(chunk)
                if total >= _MAX_BODY_BYTES:
                    break
            body = b"".join(chunks)[:_MAX_BODY_BYTES]
            text = body.decode(r.encoding or "utf-8", errors="replace")
            return r.status_code, text


class _MetaParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta: dict[str, str] = {}
        self.title: str | None = None
        self._in_title = False
        self.favicon: str | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            prop = (a.get("property") or a.get("name") or "").lower()
            content = a.get("content")
            if prop and content:
                self.meta[prop] = content
        elif tag == "link":
            rel = (a.get("rel") or "").lower()
            if "icon" in rel and a.get("href"):
                self.favicon = a["href"]
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title and self.title is None:
            self.title = data.strip()


def _parse_metadata(html: str, base_url: str) -> dict:
    p = _MetaParser()
    try:
        p.feed(html)
    except Exception:
        pass

    title = (
        p.meta.get("og:title")
        or p.meta.get("twitter:title")
        or p.title
        or base_url
    )
    description = (
        p.meta.get("og:description")
        or p.meta.get("twitter:description")
        or p.meta.get("description")
        or ""
    )
    preview = p.meta.get("og:image") or p.meta.get("twitter:image") or ""
    favicon = p.favicon or ""

    if preview and not re.match(r"^https?://", preview):
        preview = urljoin(base_url, preview)
    if favicon and not re.match(r"^https?://", favicon):
        favicon = urljoin(base_url, favicon)

    return {
        "url": base_url,
        "title": title.strip() if isinstance(title, str) else base_url,
        "description": description.strip() if isinstance(description, str) else "",
        "preview_image_url": preview,
        "favicon_url": favicon,
        "fetched_at": time.time(),
    }


def _fallback(url: str) -> dict:
    return {
        "url": url,
        "title": url,
        "description": "",
        "preview_image_url": "",
        "favicon_url": "",
        "fetched_at": time.time(),
    }


async def fetch_link_metadata(url: str) -> dict:
    try:
        status, body = await asyncio.wait_for(_http_get(url), timeout=_TIMEOUT + 0.5)
    except (asyncio.TimeoutError, TimeoutError):
        logger.info("canvas unfurl timeout for %s", url)
        return _fallback(url)
    except Exception:
        logger.info("canvas unfurl error for %s", url, exc_info=True)
        return _fallback(url)
    if status >= 400:
        return _fallback(url)
    try:
        return _parse_metadata(body, url)
    except Exception:
        logger.info("canvas unfurl parse error for %s", url, exc_info=True)
        return _fallback(url)
