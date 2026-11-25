"""The browser proxy's CSS url() rewriting runs in linear time.

The pattern used to put two overlapping whitespace runs around the value, so a
few KB of crafted CSS in any style attribute or style tag froze the controller
event loop for minutes. The rewrite now has no overlapping parts.
"""
from __future__ import annotations

import time
from urllib.parse import quote

import pytest

from tinyagentos.routes.desktop_browser.rewriter import rewrite_html


def _proxy(url: str) -> str:
    return f"/api/desktop/browser/proxy?profile_id=p&url={quote(url, safe='')}"


@pytest.mark.parametrize(
    "name, style",
    [
        ("spaces_then_value", "url(" + " " * 2500 + "x"),
        ("spaces_no_close", "url(" + " " * 2500),
        ("repeated_open", "url(" * 3000),
        ("tabs", "url(" + "\t" * 2500 + "x"),
    ],
)
def test_pathological_url_input_is_bounded(name, style):
    html = f'<html><body><div style="{style}"></div><style>{style}</style></body></html>'.encode()
    start = time.perf_counter()
    rewrite_html(html, base_url="https://example.com/", proxy=_proxy)
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"{name} took {elapsed:.1f}s (must be < 2s)"


@pytest.mark.parametrize(
    "css, expect",
    [
        ("a{background:url(/bg.png)}", "https%3A%2F%2Fexample.com%2Fbg.png"),
        ('a{background:url("/bg.png")}', "https%3A%2F%2Fexample.com%2Fbg.png"),
        ("a{background:url( '/bg.png' )}", "https%3A%2F%2Fexample.com%2Fbg.png"),
        ('a{background:url("/a(1).png")}', "https%3A%2F%2Fexample.com%2Fa%281%29.png"),
    ],
)
def test_url_forms_are_still_rewritten(css, expect):
    html = f"<html><head><style>{css}</style></head></html>".encode()
    out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy).decode()
    assert expect in out
    assert "url(/bg.png)" not in out


def test_quote_style_is_preserved():
    html = b"<html><head><style>a{background:url('/x.png')}</style></head></html>"
    out = rewrite_html(html, base_url="https://example.com/", proxy=_proxy).decode()
    assert "url('/api/desktop/browser/proxy" in out
