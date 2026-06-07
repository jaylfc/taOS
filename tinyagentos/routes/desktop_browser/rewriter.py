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


# Kept in sync with the proxy route path in proxy.py. GET forms submit to this
# bare path (their query string is replaced by the form's own fields on
# submit), carrying the taOS routing params as hidden inputs instead.
_PROXY_PATH = "/api/desktop/browser/proxy"
# Reserved hidden-input names for GET-form routing — prefixed so they can't
# collide with a site's own form field named e.g. "url" or "profile_id".
_FORM_PID_FIELD = "__taos_pid"
_FORM_URL_FIELD = "__taos_url"


def rewrite_html(
    html_bytes: bytes,
    *,
    base_url: str,
    proxy: Callable[[str], str],
    charset: str = "utf-8",
    profile_id: str | None = None,
) -> bytes:
    """Rewrite all URL-bearing references in `html_bytes` to proxied form.

    `charset` is the encoding the upstream bytes are actually in (resolved
    by the proxy from the Content-Type header / <meta charset>). We feed it
    to the lxml parser so non-UTF-8 pages (Latin-1, windows-1252, …) decode
    correctly. The output is always re-serialized as UTF-8 bytes, and any
    stale `<meta charset>` is normalized to utf-8 so the iframe never
    re-guesses the encoding.

    Returns the rewritten HTML as bytes. Empty input returns empty.
    """
    if not html_bytes:
        return b""

    # lxml is forgiving — even malformed HTML produces a tree. We use
    # `fromstring` rather than `parse` because we're working with bytes
    # in memory. The parser's `encoding` overrides any <meta charset> in
    # the document, so we pass the charset the proxy already resolved.
    parser = lxml_html.HTMLParser(encoding=charset or "utf-8")
    try:
        tree = lxml_html.fromstring(html_bytes, parser=parser)
    except Exception:
        # Any parse failure → return input unchanged. Conservative.
        return html_bytes

    if tree is None:
        return html_bytes

    _rewrite_attributes(tree, base_url=base_url, proxy=proxy)
    _rewrite_forms(tree, base_url=base_url, proxy=proxy, profile_id=profile_id)
    _rewrite_srcset(tree, base_url=base_url, proxy=proxy)
    _rewrite_inline_styles(tree, base_url=base_url, proxy=proxy)
    _rewrite_style_tags(tree, base_url=base_url, proxy=proxy)
    _rewrite_meta_refresh(tree, base_url=base_url, proxy=proxy)
    _normalize_meta_charset(tree)

    return lxml_html.tostring(tree, encoding="utf-8")


def _normalize_meta_charset(tree) -> None:
    """Rewrite any <meta charset> / <meta http-equiv content-type> to utf-8.

    The body is re-serialized as UTF-8, so a leftover declaration of the
    original charset would tell the iframe to decode UTF-8 bytes as, say,
    Latin-1 — reintroducing mojibake even with a correct response header.
    """
    for meta in tree.iter("meta"):
        if meta.get("charset") is not None:
            meta.set("charset", "utf-8")
            continue
        http_equiv = (meta.get("http-equiv") or "").lower()
        if http_equiv == "content-type":
            content = meta.get("content") or ""
            if "charset" in content.lower():
                meta.set("content", "text/html; charset=utf-8")


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
    # NOTE: `action` is intentionally NOT here — form actions are handled by
    # _rewrite_forms, which has to special-case GET vs POST submission.
    for attr in ("href", "src"):
        for el in tree.iter():
            val = el.get(attr)
            if val is None:
                continue
            new = _rewrite_one(val, base_url=base_url, proxy=proxy)
            if new != val:
                el.set(attr, new)


def _rewrite_forms(
    tree, *, base_url: str, proxy: Callable[[str], str], profile_id: str | None,
) -> None:
    """Rewrite <form> actions so submissions route through the proxy.

    POST and GET need different treatment:

    - **POST** keeps the action URL's query string on submit, so the regular
      proxied action (``?profile_id=…&url=…``) works; the form fields ride in
      the request body (forwarded by the proxy).
    - **GET** *replaces* the action's query string with the form's own fields
      on submit, which would wipe out query-encoded routing params. So we point
      the action at the bare proxy path and carry the routing params as hidden
      inputs (reserved names), which survive in the submitted query. The proxy
      merges the remaining fields into the target URL.
    """
    for form in tree.iter("form"):
        raw_action = form.get("action")
        action_target = urljoin(base_url, raw_action) if raw_action else base_url
        if not action_target.startswith(("http://", "https://")):
            continue  # javascript:/data: etc — leave alone
        method = (form.get("method") or "get").strip().lower()
        if method == "post":
            form.set("action", proxy(action_target))
            continue
        # GET form
        form.set("action", _PROXY_PATH)
        if profile_id:
            _prepend_hidden(form, _FORM_PID_FIELD, profile_id)
        _prepend_hidden(form, _FORM_URL_FIELD, action_target)


def _prepend_hidden(form, name: str, value: str) -> None:
    """Insert a hidden <input name=value> as the form's first child."""
    inp = lxml_html.Element("input")
    inp.set("type", "hidden")
    inp.set("name", name)
    inp.set("value", value)
    form.insert(0, inp)


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
