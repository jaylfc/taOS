"""Head injector — adds copilot.js + meta tags to proxied HTML.

Inserts:
- <script src="/__taos/copilot.js"></script>     — the in-page agent
- <meta name="taos-copilot-ws" content="...">    — websocket URL the
                                                    script connects to
- <meta name="taos-page-base" content="...">     — the page's real
                                                    (un-proxied) base URL,
                                                    used to prime the SW so
                                                    it can rewrite relative
                                                    SPA fetches through the
                                                    proxy
- <meta name="taos-profile-id" content="...">    — the active profile id,
                                                    passed to the SW prime

Idempotent: re-injecting on already-injected output is a no-op.
"""
from __future__ import annotations

from lxml import html as lxml_html


_SCRIPT_SRC = "/__taos/copilot.js"
_WS_META_NAME = "taos-copilot-ws"
_PAGE_BASE_META_NAME = "taos-page-base"
_PROFILE_ID_META_NAME = "taos-profile-id"
# Read by copilot.js to emulate prefers-color-scheme for the proxied site.
_COLOR_SCHEME_META_NAME = "taos-color-scheme"


def _set_meta(head, name: str, content: str) -> None:
    """Set (overwrite or create) a named meta tag in *head*."""
    for meta in head.iter("meta"):
        if meta.get("name") == name:
            meta.set("content", content)
            return
    meta = lxml_html.Element("meta")
    meta.set("name", name)
    meta.set("content", content)
    head.insert(0, meta)


def inject_into_head(
    html_bytes: bytes,
    *,
    ws_url: str,
    page_base_url: str = "",
    profile_id: str = "",
    color_scheme: str = "",
) -> bytes:
    """Insert the copilot.js script + meta tags into the document head.

    ``page_base_url`` and ``profile_id`` prime the service worker (registered
    by copilot.js from inside the iframe) so it can rewrite relative SPA
    fetches back through the proxy. Idempotent: existing copilot script tags
    are left in place rather than duplicated.
    """
    if not html_bytes:
        return b""

    parser = lxml_html.HTMLParser(encoding="utf-8")
    try:
        tree = lxml_html.fromstring(html_bytes, parser=parser)
    except Exception:
        return html_bytes

    if tree is None:
        return html_bytes

    head = tree.find(".//head")
    if head is None:
        # Create a head and insert at the start of the document
        head = lxml_html.Element("head")
        tree.insert(0, head)

    # Idempotency check
    has_copilot = any(
        s.get("src") == _SCRIPT_SRC
        for s in head.iter("script")
    )
    if not has_copilot:
        script = lxml_html.Element("script")
        script.set("src", _SCRIPT_SRC)
        head.insert(0, script)

    # WS meta + SW prime context — overwrite existing rather than append
    _set_meta(head, _WS_META_NAME, ws_url)
    if page_base_url:
        _set_meta(head, _PAGE_BASE_META_NAME, page_base_url)
    if profile_id:
        _set_meta(head, _PROFILE_ID_META_NAME, profile_id)
    if color_scheme in ("light", "dark"):
        # Read by copilot.js (matchMedia emulation); the standard color-scheme
        # meta drives UA default surfaces (form controls, scrollbars, bg).
        _set_meta(head, _COLOR_SCHEME_META_NAME, color_scheme)
        _set_meta(head, "color-scheme", color_scheme)

    return lxml_html.tostring(tree, encoding="utf-8")
