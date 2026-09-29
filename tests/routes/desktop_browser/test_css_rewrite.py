"""Tests for CSS URL rewriting in the browser proxy rewriter."""
from __future__ import annotations

import pytest
import respx


def _proxy(url: str) -> str:
    from urllib.parse import quote
    return f"/api/desktop/browser/proxy?profile_id=p&url={quote(url, safe='')}"


class TestCssRewrite:
    def test_rewrites_import_in_style_tag(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = (
            b'<html><head><style>'
            b'@import "https://evil.example/x.css";'
            b'</style></head><body></body></html>'
        )
        out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy)

        assert b"/api/desktop/browser/proxy?url=" in out
        assert b"evil.example%2Fx.css" in out

    def test_preserves_data_uri_url_with_parentheses(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        data_uri = (
            b"url(data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'>"
            b"<text fill='rgb(0,0,0)'>x</text></svg>)"
        )
        html = (
            b'<html><body><div style="background-image: ' + data_uri + b'"></div></body></html>'
        )
        out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy)

        assert b"url(data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'><text fill='rgb(0,0,0)'>x</text></svg>)" in out


@pytest.mark.asyncio
class TestProxyCssRewrite:
    @respx.mock
    async def test_rewrites_text_css_response_body(self, client):
        from httpx import Response
        from unittest.mock import patch

        css_body = b'@import "https://evil.example/x.css";\n.foo { background: url(/img.png); }'
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
        body = resp.content.decode("utf-8")
        assert "/api/desktop/browser/proxy" in body
        assert "evil.example%2Fx.css" in body
        assert "example.com%2Fimg.png" in body
