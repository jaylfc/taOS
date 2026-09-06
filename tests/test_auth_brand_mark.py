from __future__ import annotations

import re

from tinyagentos.routes.auth import _login_page, _setup_page

# Bare Unicode glyphs that have historically been used as the taOS brand mark on
# the server-rendered auth pages. They are host-font-dependent: a machine with
# no font covering the code point renders a missing-glyph box (TOFU) instead of
# the logo. The auth pages are deliberately JS-free and CDN-free so they work on
# any device, so the mark must never regress to a text glyph.
_BRAND_GLYPHS = ("⌗", "✦")


_BRAND_OPEN = re.compile(r'<div\b[^>]*\bclass="[^"]*\bbrand\b[^"]*"[^>]*>', re.DOTALL)
_DIV_EDGE = re.compile(r"<div\b|</div\s*>", re.IGNORECASE)


def _brand_inner(html: str) -> str:
    """Return the inner HTML of the `.brand` block.

    Anchors every assertion on the brand block itself rather than the whole
    page: the auth pages carry other markup, so a page-level check would be one
    level coarser than the defect and could not fail on a mark rendered here.

    Matches the opening tag on the `brand` class token rather than on the exact
    string `<div class="brand">`, so a modifier class or an added attribute does
    not turn every assertion below into a "no .brand element" error, and walks
    nested `<div>`s to the *matching* close. A non-greedy `(.*?)</div>` would
    stop at the first close tag instead: nest one `<div>` in the brand block and
    the drawn-mark assertions would only ever scan the part before it, which is
    the shape that lets a mark come back while the test stays green.
    """
    open_tag = _BRAND_OPEN.search(html)
    assert open_tag, "no .brand element found in page"

    depth = 1
    pos = open_tag.end()
    for edge in _DIV_EDGE.finditer(html, pos):
        depth += 1 if edge.group(0).lower().startswith("<div") else -1
        if depth == 0:
            return html[open_tag.end() : edge.start()]
    raise AssertionError("unbalanced .brand element: no matching </div>")


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

    def test_no_bare_glyph_anywhere_on_the_auth_pages(self):
        """The brand-scoped checks above cannot see a glyph that reappears
        outside the brand block — as a header ornament or a submit-button
        affordance, say. These pages are JS-free and CDN-free precisely so they
        render on any device, so the TOFU risk is page-wide, not brand-local.
        Kept alongside the scoped checks rather than replacing them: this one
        cannot tell us the *mark* regressed, only that a glyph is present.
        """
        for name, page in (("login", _login_page()), ("setup", _setup_page())):
            for glyph in _BRAND_GLYPHS:
                assert glyph not in page, f"bare glyph {glyph!r} is back on the {name} page"
