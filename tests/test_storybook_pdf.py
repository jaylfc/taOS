from pathlib import Path

from PIL import Image

from tinyagentos.projects.storybook import render_storybook_pdf, _wrap, _fit_cover
from PIL import ImageDraw, ImageFont


def _img(tmp: Path, name: str, colour) -> Path:
    p = tmp / name
    Image.new("RGB", (300, 200), colour).save(p)
    return p


def test_renders_multipage_pdf(tmp_path: Path):
    a = _img(tmp_path, "a.png", (200, 120, 60))
    b = _img(tmp_path, "b.png", (60, 140, 200))
    out = tmp_path / "book.pdf"
    res = render_storybook_pdf(
        title="Brave Little Fox",
        author="taOS",
        pages=[
            {"text": "Once upon a time a small fox lived under a great oak tree.", "image": a},
            {"text": "Every morning the fox set off to explore the bright forest.", "image": b},
        ],
        out_path=out,
    )
    assert res == out
    assert out.is_file()
    data = out.read_bytes()
    assert data[:5] == b"%PDF-"  # valid PDF header
    assert len(data) > 1000      # non-trivial


def test_handles_missing_image_and_empty_text(tmp_path: Path):
    out = tmp_path / "book.pdf"
    render_storybook_pdf(
        title="No Art",
        pages=[{"text": "", "image": None}, {"text": "page two", "image": tmp_path / "nope.png"}],
        out_path=out,
    )
    assert out.is_file() and out.read_bytes()[:5] == b"%PDF-"


def test_cover_defaults_to_first_page_image(tmp_path: Path):
    a = _img(tmp_path, "a.png", (10, 200, 10))
    out = tmp_path / "book.pdf"
    # No explicit cover_image -> uses pages[0].image, should not raise.
    render_storybook_pdf(title="T", pages=[{"text": "x", "image": a}], out_path=out)
    assert out.is_file()


def test_wrap_breaks_long_text():
    img = Image.new("RGB", (100, 100))
    d = ImageDraw.Draw(img)
    f = ImageFont.load_default()
    lines = _wrap(d, "word " * 50, f, 120)
    assert len(lines) > 1


def test_fit_cover_fills_box_exactly():
    src = Image.new("RGB", (400, 100))
    out = _fit_cover(src, 200, 200)
    assert out.size == (200, 200)
