"""Tests for CSS rewriting in the desktop browser proxy.

These tests target the three gaps identified in the lib-audit:
  1. @import in <style> blocks is never rewritten
  2. text/css responses are passed through byte-for-byte
  3. url() data-URIs containing parentheses are truncated by the regex
"""
from __future__ import annotations

import pytest
from httpx import Response
from unittest.mock import patch

import respx


def _proxy(url: str) -> str:
    from urllib.parse import quote
    return f"/api/desktop/browser/proxy?profile_id=p&url={quote(url, safe='')}"


class TestCssRewriter:
    def test_rewrites_import_in_style_tag(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = (
            b'<html><head><style>'
            b'@import "https://evil.example/x.css";'
            b'</style></head><body></body></html>'
        )
        out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy)

        assert b"/api/desktop/browser/proxy" in out
        assert b"https%3A%2F%2Fevil.example%2Fx.css" in out

    def test_preserves_data_uri_url_with_parentheses(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        # Data URI with parentheses in the SVG content — must survive intact.
        # No inner quotes so it is valid CSS inside url("...").
        data_uri = "data:image/svg+xml,<svg><rect fill=rgb(0,0,0)/></svg>"
        html = (
            b'<html><head><style>.x { background: url("'
            + data_uri.encode("utf-8")
            + b'"); }</style></head><body></body></html>'
        )
        out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy)

        assert b'url("' + data_uri.encode("utf-8") + b'")' in out

        assert b"data:image/svg+xml" in out
        assert b"rgb(0,0,0)" in out


@pytest.mark.asyncio
class TestProxyFetchCss:
    @respx.mock
    async def test_rewrites_text_css_response_body(self, client):
        respx.get("http://example.com/style.css").mock(
            return_value=Response(
                200,
                content=(
                    b'@import "https://evil.example/x.css";\n'
                    b'body { background: url("/bg.png"); }'
                ),
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
        assert b"https%3A%2F%2Fevil.example%2Fx.css" in resp.content
        assert b"http%3A%2F%2Fexample.com%2Fbg.png" in resp.content
