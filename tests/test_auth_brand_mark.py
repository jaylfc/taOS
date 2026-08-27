from __future__ import annotations

import re

from tinyagentos.routes.auth import _login_page, _setup_page

# Bare Unicode glyphs that have historically been used as the taOS brand mark on
# the server-rendered auth pages. They are host-font-dependent: a machine with
# no font covering the code point renders a missing-glyph box (TOFU) instead of
# the logo. The auth pages are deliberately JS-free and CDN-free so they work on
# any device, so the mark must be an inline SVG rather than a text glyph.
_BRAND_GLYPHS = ("⌗", "✦")


def _icon_inner(html: str) -> str:
    """Return the inner HTML of the `.icon` brand element.

    Anchors the assertion on the brand element itself rather than on the whole
    page -- the pages already contain other markup, so a page-level "svg"
    check would be one level too coarse to fail on this defect.
    """
    match = re.search(r'<div class="icon">(.*?)</div>', html, re.DOTALL)
    assert match, "no .icon brand element found in page"
    return match.group(1)


class TestAuthBrandMark:
    def test_login_page_brand_is_inline_svg(self):
        icon = _icon_inner(_login_page())
        assert "<svg" in icon, "brand mark is a bare glyph"
        for glyph in _BRAND_GLYPHS:
            assert glyph not in icon, f"bare brand glyph {glyph!r} still present in icon"

    def test_setup_page_brand_is_inline_svg(self):
        icon = _icon_inner(_setup_page())
        assert "<svg" in icon, "brand mark is a bare glyph"
        for glyph in _BRAND_GLYPHS:
            assert glyph not in icon, f"bare brand glyph {glyph!r} still present in icon"
