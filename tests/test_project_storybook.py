from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont

from tinyagentos.projects.storybook import (
    PAGE_H,
    PAGE_W,
    _fit_cover,
    _load_font,
    _open,
    _placeholder,
    _render_cover,
    _render_page,
    _wrap,
    render_storybook_pdf,
)


def _img(tmp: Path, name: str, size: tuple[int, int], colour) -> Path:
    p = tmp / name
    Image.new("RGB", size, colour).save(p)
    return p


def test_open_returns_none_for_missing_or_empty():
    assert _open(None) is None
    assert _open("") is None
    assert _open("/no/such/file.png") is None


def test_open_loads_valid_image(tmp_path: Path):
    p = _img(tmp_path, "art.png", (120, 80), (40, 80, 120))
    img = _open(p)
    assert img is not None
    assert img.size == (120, 80)
    assert img.mode == "RGB"


def test_placeholder_matches_box_dimensions():
    img = _placeholder(400, 300)
    assert img.size == (400, 300)
    assert img.mode == "RGB"


def test_render_cover_without_art_uses_placeholder():
    page = _render_cover("Brave Fox", author="taOS", cover=None)
    assert page.size == (PAGE_W, PAGE_H)


def test_render_cover_includes_author_line():
    page = _render_cover("Title", author="Jay", cover=None)
    # Spot-check pixels differ from a cover with no author (title-only layout).
    no_author = _render_cover("Title", author=None, cover=None)
    assert page.tobytes() != no_author.tobytes()


def test_render_cover_defaults_untitled():
    page = _render_cover("", author=None, cover=None)
    assert page.size == (PAGE_W, PAGE_H)


def test_render_page_numbers_and_caption(tmp_path: Path):
    art = _img(tmp_path, "p1.png", (600, 400), (200, 100, 50))
    page = _render_page("The fox ran fast.", art, number=3)
    assert page.size == (PAGE_W, PAGE_H)
    # Bottom band should differ from a page with different text.
    other = _render_page("Different caption.", art, number=3)
    assert page.tobytes() != other.tobytes()


def test_render_page_uses_placeholder_when_image_missing(tmp_path: Path):
    page = _render_page("text only", tmp_path / "missing.png", number=1)
    assert page.size == (PAGE_W, PAGE_H)


def test_load_font_returns_usable_font():
    font = _load_font(24, ("/no/such/font.ttf",))
    draw = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    assert draw.textlength("x", font=font) >= 0


def test_wrap_empty_text_returns_blank_line():
    draw = ImageDraw.Draw(Image.new("RGB", (100, 100)))
    font = ImageFont.load_default()
    assert _wrap(draw, "", font, 200) == [""]


def test_wrap_single_long_word_splits_to_one_line():
    draw = ImageDraw.Draw(Image.new("RGB", (100, 100)))
    font = ImageFont.load_default()
    lines = _wrap(draw, "supercalifragilistic", font, 10)
    assert len(lines) == 1
    assert lines[0] == "supercalifragilistic"


def test_fit_cover_portrait_and_landscape_fill_box():
    portrait = Image.new("RGB", (100, 300))
    landscape = Image.new("RGB", (300, 100))
    assert _fit_cover(portrait, 200, 200).size == (200, 200)
    assert _fit_cover(landscape, 200, 200).size == (200, 200)


def test_render_storybook_pdf_creates_parent_dirs(tmp_path: Path):
    out = tmp_path / "nested" / "exports" / "book.pdf"
    render_storybook_pdf(title="Empty", pages=[], out_path=out)
    assert out.is_file()
    assert out.read_bytes()[:5] == b"%PDF-"


def test_render_storybook_pdf_honors_explicit_cover_image(tmp_path: Path):
    page_img = _img(tmp_path, "page.png", (300, 200), (10, 20, 30))
    cover_img = _img(tmp_path, "cover.png", (300, 200), (200, 10, 10))
    out = tmp_path / "book.pdf"
    render_storybook_pdf(
        title="Cover Test",
        pages=[{"text": "one", "image": page_img}],
        out_path=out,
        cover_image=cover_img,
    )
    assert out.is_file()


def test_render_storybook_pdf_multipage_structure(tmp_path: Path):
    a = _img(tmp_path, "a.png", (400, 300), (255, 0, 0))
    b = _img(tmp_path, "b.png", (400, 300), (0, 255, 0))
    out = tmp_path / "book.pdf"
    path = render_storybook_pdf(
        title="Two Pages",
        author="Agent",
        pages=[
            {"text": "First page caption.", "image": a},
            {"text": "Second page caption.", "image": b},
        ],
        out_path=out,
    )
    assert path == out
    data = out.read_bytes()
    assert data[:5] == b"%PDF-"
    assert len(data) > 2000