"""Server-side PNG rendering of a project canvas.

This is intentionally a low-fidelity Pillow renderer — we draw bounding
boxes coloured by element kind plus first-line labels. Vision-capable
agents read this to "see" the board. A pixel-perfect tldraw render via
headless browser is a follow-up; the simple renderer is enough to
distinguish layout, kind, and labels.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_MIN_WIDTH = 800
_MIN_HEIGHT = 600
_PADDING = 40

_KIND_FILL = {
    "note":   (255, 240, 140, 255),
    "link":   (190, 215, 255, 255),
    "image":  (220, 220, 220, 255),
    "user_shape": (245, 245, 245, 255),
}
_KIND_OUTLINE = {
    "note":   (200, 180, 60, 255),
    "link":   (90, 130, 220, 255),
    "image":  (140, 140, 140, 255),
    "user_shape": (180, 180, 180, 255),
}


def _bounds(elements: list[dict]) -> tuple[float, float, float, float]:
    if not elements:
        return 0, 0, _MIN_WIDTH, _MIN_HEIGHT
    xs = [e["x"] for e in elements]
    ys = [e["y"] for e in elements]
    rights = [e["x"] + e["w"] for e in elements]
    bottoms = [e["y"] + e["h"] for e in elements]
    return min(xs), min(ys), max(rights), max(bottoms)


def _label_for(el: dict) -> str:
    kind = el.get("kind", "")
    payload = el.get("payload") or {}
    if kind == "note":
        return str(payload.get("text", ""))[:60]
    if kind == "link":
        return str(payload.get("title") or payload.get("url") or "")[:60]
    if kind == "image":
        return str(payload.get("alt", "image"))[:40]
    return kind


def render_snapshot_png(*, elements: list[dict], output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    min_x, min_y, max_x, max_y = _bounds(elements)
    width = max(_MIN_WIDTH, int(max_x - min_x) + 2 * _PADDING)
    height = max(_MIN_HEIGHT, int(max_y - min_y) + 2 * _PADDING)
    img = Image.new("RGB", (width, height), color=(255, 255, 255))
    draw = ImageDraw.Draw(img, "RGBA")
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    for el in elements:
        kind = el.get("kind", "user_shape")
        x = int(el["x"] - min_x + _PADDING)
        y = int(el["y"] - min_y + _PADDING)
        w = int(el["w"])
        h = int(el["h"])
        fill = _KIND_FILL.get(kind, _KIND_FILL["user_shape"])
        outline = _KIND_OUTLINE.get(kind, _KIND_OUTLINE["user_shape"])
        draw.rectangle([x, y, x + w, y + h], fill=fill, outline=outline, width=2)
        label = _label_for(el)
        if label and font is not None:
            draw.text((x + 6, y + 6), label, fill=(20, 20, 20), font=font)

    img.save(output_path, "PNG")
    return output_path
