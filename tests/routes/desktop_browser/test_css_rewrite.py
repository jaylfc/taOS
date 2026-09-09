"""Tests for CSS URL rewriting in the browser proxy.

Red-first: these tests reproduce three isolation leaks described in
tsk-fgs3yp and must fail against the current regex-based rewriter.
"""
from __future__ import annotations

import respx
import pytest


def _proxy(url: str) -> str:
    from urllib.parse import quote
    return f"/api/desktop/browser/proxy?profile_id=p&url={quote(url, safe='')}"


class TestCssRewrite:
    def test_rewrites_import_in_inline_style_block(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = (
            b'<html><head><style>'
            b'@import "https://evil.example/x.css";'
            b'</style></head><body></body></html>'
        )
        out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy)

        assert b"/api/desktop/browser/proxy" in out
        assert b"https://evil.example/x.css" not in out

    def test_rewrites_quoted_url_with_parentheses(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = (
            b"<html><body><div style='background: "
            b'url("http://example.com/img(rgb).png)\'>'
            b"</div></body></html>"
        )
        out = rewrite_html(html, base_url="http://example.com/", proxy=_proxy)

        assert b"/api/desktop/browser/proxy" in out
        assert b"img%28rgb%29.png" in out


@pytest.mark.asyncio
class TestProxyCssRewrite:
    @respx.mock
    async def test_rewrites_text_css_response_body(self, client):
        from httpx import Response
        from unittest.mock import patch

        css_body = (
            b'@import "https://evil.example/x.css";\n'
            b'body { background: url(/bg.png); }'
        )
        respx.get("http://example.com/style.css").mock(
            return_value=Response(
                200,
                content=css_body,
                headers={"content-type": "text/css"},
            )
        )

        with patch(
            "tinyagentos.routes.desktop_browser.ssrf.socket.getaddrinfo",
            return_value=[(2, 1, 6, "", ("93.184.216.34", 0))],
        ):
            resp = await client.get(
                "/api/desktop/browser/proxy",
                params={"profile_id": "personal", "url": "http://example.com/style.css"},
            )

        assert resp.status_code == 200
        assert b"/api/desktop/browser/proxy" in resp.content
        assert b"https://evil.example/x.css" not in resp.content
