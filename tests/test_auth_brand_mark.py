from __future__ import annotations

import re

from tinyagentos.routes.auth import _login_page, _setup_page

# Bare Unicode glyphs that have historically been used as the taOS brand mark on
# the server-rendered auth pages. They are host-font-dependent: a machine with
# no font covering the code point renders a missing-glyph box (TOFU) instead of
# the logo. The auth pages are deliberately JS-free and CDN-free so they work on
# any device, so the mark must never regress to a text glyph.
_BRAND_GLYPHS = ("⌗", "✦")


def _brand_inner(html: str) -> str:
    """Return the inner HTML of the `.brand` block.

    Anchors every assertion on the brand block itself rather than the whole
    page: the auth pages carry other markup, so a page-level check would be one
    level coarser than the defect and could not fail on a mark rendered here.
    """
    match = re.search(r'<div class="brand">(.*?)</div>', html, re.DOTALL)
    assert match, "no .brand element found in page"
    return match.group(1)


class TestAuthBrandMark:
    """The auth pages carry the taOS wordmark, not a drawn glyph.

    The mark these pages shipped with was a rounded square with a centre dot
    and an X through it. On a sign-in screen that reads as an error badge or a
    close affordance rather than a logo, so the brand is set as type instead.
    """

    def test_login_page_brand_is_wordmark(self):
        brand = _brand_inner(_login_page())
        assert '<h1 class="wordmark">taOS</h1>' in brand, "login page lost the taOS wordmark"

    def test_setup_page_brand_is_wordmark(self):
        brand = _brand_inner(_setup_page())
        assert '<h1 class="wordmark">taOS</h1>' in brand, "setup page lost the taOS wordmark"

    def test_login_page_brand_has_no_drawn_mark(self):
        brand = _brand_inner(_login_page())
        assert "<svg" not in brand, "a drawn brand mark is back on the login page"
        assert "<line" not in brand, "the crossing-lines (X) mark is back on the login page"
        assert 'class="icon"' not in brand, "the brand icon tile is back on the login page"

    def test_setup_page_brand_has_no_drawn_mark(self):
        brand = _brand_inner(_setup_page())
        assert "<svg" not in brand, "a drawn brand mark is back on the setup page"
        assert "<line" not in brand, "the crossing-lines (X) mark is back on the setup page"
        assert 'class="icon"' not in brand, "the brand icon tile is back on the setup page"

    def test_brand_does_not_regress_to_a_bare_glyph(self):
        """Dropping the drawn mark must not reintroduce the TOFU-prone glyphs
        it originally replaced."""
        for page in (_login_page(), _setup_page()):
            brand = _brand_inner(page)
            for glyph in _BRAND_GLYPHS:
                assert glyph not in brand, f"bare brand glyph {glyph!r} is back in the brand block"
