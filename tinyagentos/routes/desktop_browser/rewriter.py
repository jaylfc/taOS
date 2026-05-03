"""lxml-based DOM URL rewriter for the BrowserApp proxy.

Walks the parsed HTML and rewrites every URL-bearing attribute so it
points at the proxy endpoint. The caller supplies a `proxy(url)`
callable that turns an absolute URL into the proxied form (the proxy
endpoint owns the user_id/profile_id binding).

What we rewrite:
- href / src / action attributes on every tag
- srcset (each URL in the comma-separated list, preserving descriptors)
- inline style="background-image: url(...)" (and url('...'), url("..."))
- <style> tag contents
- <meta http-equiv="refresh" content="N;url=...">

What we don't rewrite:
- data: / javascript: / mailto: / tel: / blob: / about: schemes
- # fragment-only hrefs
- already-proxied URLs (idempotent)

What's intentionally deferred to the Service Worker (PR 8):
- JS-runtime URLs (fetch, new URL, dynamic import)
- URLs computed at runtime via string concatenation in JS

The rewriter is server-side and operates on the response bytes.
SPAs that use JS routing will have nav links rewritten correctly but
their internal API calls (XHR/fetch) won't — until PR 8's Service
Worker intercepts them client-side.
"""
from __future__ import annotations

import re
from typing import Callable
from urllib.parse import urljoin

from lxml import html as lxml_html


# Schemes we never rewrite — these aren't HTTP fetches the proxy can serve.
_SKIP_PREFIXES = (
    "data:", "javascript:", "mailto:", "tel:", "blob:", "about:", "#",
)


# CSS url() rewriter — captures url(), url(""), url('').
_CSS_URL_RE = re.compile(
    r"""url\(\s*(['"]?)([^)'"]+)\1\s*\)""",
    re.IGNORECASE,
)


def rewrite_html(
    html_bytes: bytes,
    *,
    base_url: str,
    proxy: Callable[[str], str],
) -> bytes:
    """Rewrite all URL-bearing references in `html_bytes` to proxied form.

    Returns the rewritten HTML as bytes. Empty input returns empty.
    """
    if not html_bytes:
        return b""

    # lxml is forgiving — even malformed HTML produces a tree. We use
    # `fromstring` rather than `parse` because we're working with bytes
    # in memory, and we explicitly handle the encoding via the parser.
    parser = lxml_html.HTMLParser(encoding="utf-8")
    try:
        tree = lxml_html.fromstring(html_bytes, parser=parser)
    except Exception:
        # Any parse failure → return input unchanged. Conservative.
        return html_bytes

    if tree is None:
        return html_bytes

    _rewrite_attributes(tree, base_url=base_url, proxy=proxy)
    _rewrite_srcset(tree, base_url=base_url, proxy=proxy)
    _rewrite_inline_styles(tree, base_url=base_url, proxy=proxy)
    _rewrite_style_tags(tree, base_url=base_url, proxy=proxy)
    _rewrite_meta_refresh(tree, base_url=base_url, proxy=proxy)

    return lxml_html.tostring(tree, encoding="utf-8")


def _should_rewrite(url: str) -> bool:
    if not url:
        return False
    return not any(url.startswith(p) for p in _SKIP_PREFIXES)


def _rewrite_one(url: str, *, base_url: str, proxy: Callable[[str], str]) -> str:
    if not _should_rewrite(url):
        return url
    absolute = urljoin(base_url, url)
    if not absolute.startswith(("http://", "https://")):
        return url
    return proxy(absolute)


def _rewrite_attributes(
    tree, *, base_url: str, proxy: Callable[[str], str],
) -> None:
    for attr in ("href", "src", "action"):
        for el in tree.iter():
            val = el.get(attr)
            if val is None:
                continue
            new = _rewrite_one(val, base_url=base_url, proxy=proxy)
            if new != val:
                el.set(attr, new)


def _rewrite_srcset(
    tree, *, base_url: str, proxy: Callable[[str], str],
) -> None:
    for el in tree.iter():
        srcset = el.get("srcset")
        if not srcset:
            continue
        # srcset is "url descriptor, url descriptor, …" — split, rewrite
        # each url, preserve descriptor
        parts = []
        for entry in srcset.split(","):
            entry = entry.strip()
            if not entry:
                continue
            tokens = entry.split(None, 1)
            url = tokens[0]
            descriptor = tokens[1] if len(tokens) > 1 else ""
            new_url = _rewrite_one(url, base_url=base_url, proxy=proxy)
            parts.append(
                f"{new_url} {descriptor}".strip()
            )
        el.set("srcset", ", ".join(parts))


def _rewrite_css_text(
    text: str, *, base_url: str, proxy: Callable[[str], str],
) -> str:
    def replace(match: re.Match) -> str:
        quote = match.group(1)
        url = match.group(2).strip()
        new_url = _rewrite_one(url, base_url=base_url, proxy=proxy)
        return f"url({quote}{new_url}{quote})"

    return _CSS_URL_RE.sub(replace, text)


def _rewrite_inline_styles(
    tree, *, base_url: str, proxy: Callable[[str], str],
) -> None:
    for el in tree.iter():
        style = el.get("style")
        if not style or "url(" not in style.lower():
            continue
        el.set("style", _rewrite_css_text(style, base_url=base_url, proxy=proxy))


def _rewrite_style_tags(
    tree, *, base_url: str, proxy: Callable[[str], str],
) -> None:
    for style_el in tree.iter("style"):
        if style_el.text is None or "url(" not in style_el.text.lower():
            continue
        style_el.text = _rewrite_css_text(
            style_el.text, base_url=base_url, proxy=proxy
        )


def _rewrite_meta_refresh(
    tree, *, base_url: str, proxy: Callable[[str], str],
) -> None:
    for meta in tree.iter("meta"):
        http_equiv = (meta.get("http-equiv") or "").lower()
        if http_equiv != "refresh":
            continue
        content = meta.get("content") or ""
        # content is "N;url=..."
        if "url=" not in content.lower():
            continue
        # Split on the first url= (case-insensitive)
        idx = content.lower().find("url=")
        prefix = content[: idx + 4]  # includes "url="
        url = content[idx + 4 :].strip().strip("'\"")
        new_url = _rewrite_one(url, base_url=base_url, proxy=proxy)
        meta.set("content", f"{prefix}{new_url}")
