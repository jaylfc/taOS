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


class TestCharsetHandling:
    def test_utf8_copyright_and_nbsp_survive_roundtrip(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = (
            "<html><head><meta charset=\"utf-8\"></head>"
            "<body><p>© 2026 Example Co</p></body></html>"
        ).encode("utf-8")
        out = rewrite_html(
            html, base_url="https://example.com/", proxy=_proxy, charset="utf-8"
        )
        # Real UTF-8 bytes for © and nbsp present; no Latin-1 mojibake.
        assert "©".encode("utf-8") in out
        assert " ".encode("utf-8") in out
        # The mojibake signature (Â©) would be the bytes for U+00C2 U+00A9.
        assert "Â©".encode("utf-8") not in out
        # Output is valid UTF-8.
        out.decode("utf-8")

    def test_latin1_page_decoded_with_declared_charset(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        # Raw 0xa9 is © in ISO-8859-1.
        html = (
            b"<html><head><meta charset=\"iso-8859-1\"></head>"
            b"<body><p>\xa9 2026</p></body></html>"
        )
        out = rewrite_html(
            html, base_url="https://example.com/", proxy=_proxy, charset="ISO-8859-1"
        )
        text = out.decode("utf-8")
        assert "© 2026" in text

    def test_meta_charset_normalized_to_utf8(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = (
            b"<html><head><meta charset=\"iso-8859-1\"></head>"
            b"<body><p>x</p></body></html>"
        )
        out = rewrite_html(
            html, base_url="https://example.com/", proxy=_proxy, charset="ISO-8859-1"
        )
        lower = out.lower()
        assert b"charset=\"utf-8\"" in lower
        assert b"iso-8859-1" not in lower


class TestFormRewriting:
    """Forms route through the proxy; GET and POST need different handling."""

    def test_get_form_uses_bare_path_and_hidden_inputs(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = (
            b'<html><body><form action="/search" method="get">'
            b'<input name="q"></form></body></html>'
        )
        out = rewrite_html(
            html, base_url="https://www.google.com/", proxy=_proxy, profile_id="p",
        ).decode()

        # Bare proxy path (no query — a GET submit would clobber it anyway).
        assert 'action="/api/desktop/browser/proxy"' in out
        # Routing carried as reserved hidden inputs that survive the submit.
        assert 'name="__taos_url"' in out
        assert 'value="https://www.google.com/search"' in out
        assert 'name="__taos_pid"' in out
        assert 'value="p"' in out

    def test_get_form_defaults_when_no_method(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = b'<html><body><form action="/s"><input name="q"></form></body></html>'
        out = rewrite_html(
            html, base_url="https://ex.com/", proxy=_proxy, profile_id="p",
        ).decode()
        assert 'action="/api/desktop/browser/proxy"' in out
        assert 'value="https://ex.com/s"' in out

    def test_post_form_keeps_query_encoded_action(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = (
            b'<html><body><form action="/login" method="POST">'
            b'<input name="u"></form></body></html>'
        )
        out = rewrite_html(
            html, base_url="https://ex.com/", proxy=_proxy, profile_id="p",
        ).decode()
        # POST keeps the query-encoded proxied action; no hidden routing inputs.
        assert "url=https%3A%2F%2Fex.com%2Flogin" in out
        assert "__taos_url" not in out

    def test_form_without_action_targets_current_page(self):
        from tinyagentos.routes.desktop_browser.rewriter import rewrite_html

        html = b'<html><body><form><input name="q"></form></body></html>'
        out = rewrite_html(
            html, base_url="https://ex.com/page", proxy=_proxy, profile_id="p",
        ).decode()
        assert 'value="https://ex.com/page"' in out
