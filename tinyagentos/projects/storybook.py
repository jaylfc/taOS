"""Render a project's pages + illustrations into an illustrated storybook PDF.

Pure Pillow (already a dependency): each page is composed as a designed image
(full-bleed illustration with a caption band) and the pages are assembled into a
multi-page PDF. No new dependencies, works offline on the controller.

The agent calls this via the export_storybook tool after it has generated the
art (generate_image) and placed it on the project canvas. The book content is
an ordered list of pages, each an illustration plus its caption, with a cover.
"""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont

# 4:5 portrait, a comfortable picture-book page at print-ish density.
PAGE_W, PAGE_H = 1200, 1500
_MARGIN = 64
_PAGE_BG = (250, 249, 246)        # warm off-white paper
_INK = (28, 28, 30)
_MUTED = (90, 90, 96)
_CAPTION_BG = (255, 255, 255)

# System fonts to try, in preference order, before falling back to Pillow's
# bitmap default. Covers Debian/Fedora (DejaVu) and macOS.
_SERIF_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/dejavu/DejaVuSerif.ttf",
    "/Library/Fonts/Georgia.ttf",
    "/System/Library/Fonts/Supplemental/Georgia.ttf",
)
_SANS_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)
_SANS_BOLD_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
)


def _load_font(size: int, candidates: Sequence[str]) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in candidates:
        try:
            if Path(path).is_file():
                return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: int) -> list[str]:
    """Greedy word-wrap to max_w pixels."""
    words = (text or "").split()
    lines: list[str] = []
    cur = ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines or [""]


def _fit_cover(img: Image.Image, box_w: int, box_h: int) -> Image.Image:
    """Scale + center-crop the illustration to fill box_w x box_h (cover)."""
    img = img.convert("RGB")
    scale = max(box_w / img.width, box_h / img.height)
    new = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))))
    left = (new.width - box_w) // 2
    top = (new.height - box_h) // 2
    return new.crop((left, top, left + box_w, top + box_h))


def _open(image: Path | str | None) -> Image.Image | None:
    if not image:
        return None
    try:
        p = Path(image)
        if not p.is_file():
            return None
        return Image.open(p).convert("RGB")
    except Exception:
        return None


def _placeholder(box_w: int, box_h: int) -> Image.Image:
    """A soft placeholder when a page has no illustration."""
    img = Image.new("RGB", (box_w, box_h), (232, 230, 224))
    d = ImageDraw.Draw(img)
    f = _load_font(40, _SANS_CANDIDATES)
    msg = "illustration"
    w = d.textlength(msg, font=f)
    d.text(((box_w - w) / 2, box_h / 2 - 20), msg, fill=(160, 158, 150), font=f)
    return img


def _render_cover(title: str, author: str | None, cover: Path | str | None) -> Image.Image:
    page = Image.new("RGB", (PAGE_W, PAGE_H), _PAGE_BG)
    art = _open(cover)
    # Cover art fills the upper ~72% of the page.
    art_h = int(PAGE_H * 0.72)
    img = _fit_cover(art, PAGE_W, art_h) if art else _placeholder(PAGE_W, art_h)
    page.paste(img, (0, 0))
    d = ImageDraw.Draw(page)
    title_font = _load_font(78, _SANS_BOLD_CANDIDATES)
    y = art_h + 56
    for line in _wrap(d, title or "Untitled", title_font, PAGE_W - 2 * _MARGIN):
        w = d.textlength(line, font=title_font)
        d.text(((PAGE_W - w) / 2, y), line, fill=_INK, font=title_font)
        y += 92
    if author:
        a_font = _load_font(38, _SERIF_CANDIDATES)
        line = f"by {author}"
        w = d.textlength(line, font=a_font)
        d.text(((PAGE_W - w) / 2, y + 8), line, fill=_MUTED, font=a_font)
    return page


def _render_page(text: str, image: Path | str | None, number: int) -> Image.Image:
    page = Image.new("RGB", (PAGE_W, PAGE_H), _PAGE_BG)
    art_h = int(PAGE_H * 0.66)
    art = _open(image)
    img = _fit_cover(art, PAGE_W, art_h) if art else _placeholder(PAGE_W, art_h)
    page.paste(img, (0, 0))
    d = ImageDraw.Draw(page)
    # Caption band fills the rest.
    band_top = art_h
    d.rectangle([0, band_top, PAGE_W, PAGE_H], fill=_CAPTION_BG)
    body = _load_font(40, _SERIF_CANDIDATES)
    lines = _wrap(d, text, body, PAGE_W - 2 * _MARGIN)
    line_h = 56
    block_h = len(lines) * line_h
    y = band_top + max(_MARGIN, ((PAGE_H - band_top) - block_h) // 2 - 10)
    for line in lines:
        d.text((_MARGIN, y), line, fill=_INK, font=body)
        y += line_h
    # Page number, bottom-center.
    pn = _load_font(28, _SANS_CANDIDATES)
    s = str(number)
    w = d.textlength(s, font=pn)
    d.text(((PAGE_W - w) / 2, PAGE_H - 48), s, fill=_MUTED, font=pn)
    return page


def render_storybook_pdf(
    title: str,
    pages: list[dict],
    out_path: Path | str,
    cover_image: Path | str | None = None,
    author: str | None = None,
) -> Path:
    """Render an illustrated storybook PDF.

    pages: ordered list of {"text": str, "image": path|None}. cover_image
    defaults to the first page's image when not given. Returns out_path.
    """
    if cover_image is None and pages:
        cover_image = pages[0].get("image")
    rendered = [_render_cover(title, author, cover_image)]
    for i, pg in enumerate(pages, start=1):
        rendered.append(_render_page(pg.get("text", ""), pg.get("image"), i))

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    rendered[0].save(
        out, format="PDF", save_all=True, append_images=rendered[1:], resolution=150.0
    )
    return out
