"""Tests for the lxml-based DOM URL rewriter."""
from __future__ import annotations

import pytest


# The rewriter takes (html_bytes, base_url, proxy_prefix_builder) and
# returns rewritten html_bytes. proxy_prefix_builder(absolute_url) ->
# proxied_url is provided by the caller (the proxy endpoint) so the
# rewriter doesn't need to know about user_id/profile_id.


def _proxy(url: str) -> str:
    """Test stand-in for the real proxy URL builder."""
    from urllib.parse import quote
    return f"/api/desktop/browser/proxy?profile_id=p&url={quote(url, safe='')}"


class TestAttributeRewriting:
    @pytest.mark.parametrize("attr,tag", [
        ("href", "a"),
        ("href", "link"),
        ("src", "img"),
        ("src", "script"),
        ("src", "iframe"),
        ("action", "form"),
    ])
    def test_rewrites_relative_url(self, attr, tag):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = f'<html><body><{tag} {attr}="/foo"></{tag}></body></html>'.encode()
        out = rewrite_html(html, base_url="https://example.com/page", proxy=_proxy)

        assert b"https%3A%2F%2Fexample.com%2Ffoo" in out

    def test_rewrites_absolute_url(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = b'<html><body><a href="https://github.com/x">x</a></body></html>'
        out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy)

        assert b"https%3A%2F%2Fgithub.com%2Fx" in out

    def test_does_not_rewrite_data_uri(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = b'<html><body><img src="data:image/png;base64,abc"></body></html>'
        out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy)

        assert b"data:image/png;base64,abc" in out
        assert b"/api/desktop/browser/proxy" not in out

    def test_does_not_rewrite_mailto(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = b'<html><body><a href="mailto:a@b.test">a</a></body></html>'
        out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy)

        assert b"mailto:a@b.test" in out

    def test_does_not_rewrite_anchor(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = b'<html><body><a href="#section1">jump</a></body></html>'
        out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy)

        assert b'href="#section1"' in out


class TestSrcset:
    def test_rewrites_each_url_in_srcset(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = b'''<html><body>
        <img srcset="/small.png 1x, /large.png 2x">
        </body></html>'''
        out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy)

        # Both URLs in the srcset must be rewritten
        assert b"https%3A%2F%2Fexample.com%2Fsmall.png" in out
        assert b"https%3A%2F%2Fexample.com%2Flarge.png" in out

    def test_preserves_srcset_descriptors(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = b'<html><body><img srcset="/x.png 1x"></body></html>'
        out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy)

        # Descriptor "1x" must survive after the URL is rewritten
        assert b"1x" in out


class TestStyleAndCss:
    def test_rewrites_url_in_style_attribute(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = b'<html><body><div style="background-image: url(/bg.png)"></div></body></html>'
        out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy)

        assert b"https%3A%2F%2Fexample.com%2Fbg.png" in out

    def test_rewrites_url_in_style_tag(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = b'''<html><head><style>
        .header { background-image: url("/bg.png"); }
        </style></head><body></body></html>'''
        out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy)

        assert b"https%3A%2F%2Fexample.com%2Fbg.png" in out


class TestMetaRefresh:
    def test_rewrites_meta_refresh_url(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = b'<html><head><meta http-equiv="refresh" content="0;url=/landing"></head></html>'
        out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy)

        assert b"https%3A%2F%2Fexample.com%2Flanding" in out


class TestNonHtmlInput:
    def test_returns_input_unchanged_for_invalid_html(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        # lxml is permissive — plain text is wrapped in <html><body><p>
        # but our rewriter should be defensive about empty/binary input
        out = rewrite_html(b"", base_url="https://example.com/", proxy=_proxy)
        assert out == b""
