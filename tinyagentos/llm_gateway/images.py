from __future__ import annotations

import base64
import copy
import io
import logging
import threading
from typing import Any

from PIL import Image, ImageOps

MAX_LONG_EDGE = 1568
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_SOURCE_PIXELS = 40_000_000
MAX_IMAGES_PER_REQUEST = 8

_DECODE_SLOTS = threading.BoundedSemaphore(2)

_FORMAT_MEDIA = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}

logger = logging.getLogger(__name__)


def _media_type_from_header(header: str) -> str:
    return header[len("data:"):]


def _shrink_one(url: str) -> str:
    if not url.startswith("data:") or ";base64," not in url:
        return url
    prefix, b64 = url.split(";base64,", 1)
    media_type = _media_type_from_header(prefix)
    try:
        raw = base64.b64decode(b64)
    except Exception:  # noqa: BLE001
        return url
    with _DECODE_SLOTS:
        try:
            img = Image.open(io.BytesIO(raw))
        except Exception:  # noqa: BLE001
            return url
        try:
            w, h = img.size
            # Bound decode memory before exif_transpose/thumbnail.
            if w * h > MAX_SOURCE_PIXELS:
                return url
            # Animated GIF/WebP: re-saving would keep only the first frame.
            if getattr(img, "n_frames", 1) > 1:
                return url
            long_edge = max(w, h)
            if long_edge <= MAX_LONG_EDGE and len(raw) <= MAX_IMAGE_BYTES:
                return url
            # Capture format before transpose (transpose returns format=None).
            fmt = img.format
            img = ImageOps.exif_transpose(img)
            img.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE))
            if fmt not in _FORMAT_MEDIA:
                return url
            out = io.BytesIO()
            if fmt == "JPEG" and img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            img.save(out, format=fmt)
            new_bytes = out.getvalue()
            new_media = _FORMAT_MEDIA[fmt]
            if len(new_bytes) > MAX_IMAGE_BYTES:
                has_alpha = "A" in img.mode or (
                    img.mode == "P" and "transparency" in getattr(img, "info", {})
                )
                if has_alpha:
                    work = img
                    while len(new_bytes) > MAX_IMAGE_BYTES:
                        ww, hh = work.size
                        cur_long = max(ww, hh)
                        target = max(512, int(cur_long * 0.75))
                        if target == cur_long:
                            break
                        scale = target / cur_long
                        work = work.resize(
                            (max(1, int(ww * scale)), max(1, int(hh * scale))),
                            Image.LANCZOS,
                        )
                        out = io.BytesIO()
                        work.save(out, format="PNG")
                        new_bytes = out.getvalue()
                        new_media = "image/png"
                else:
                    rgb = img.convert("RGB")
                    for quality in (85, 70):
                        out = io.BytesIO()
                        rgb.save(out, format="JPEG", quality=quality)
                        new_bytes = out.getvalue()
                        new_media = "image/jpeg"
                        if len(new_bytes) <= MAX_IMAGE_BYTES:
                            break
            new_b64 = base64.b64encode(new_bytes).decode("ascii")
            return f"data:{new_media};base64,{new_b64}"
        except Exception:  # noqa: BLE001
            return url


def has_image_parts(body: Any) -> bool:
    if not isinstance(body, dict):
        return False
    messages = body.get("messages")
    if not isinstance(messages, list):
        return False
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            image_url = part.get("image_url")
            if isinstance(image_url, str):
                url = image_url
            elif isinstance(image_url, dict):
                url = image_url.get("url")
            else:
                continue
            if isinstance(url, str) and url.startswith("data:"):
                return True
    return False


def shrink_images(body: dict) -> dict:
    new_body = copy.deepcopy(body)
    messages = new_body.get("messages")
    if not isinstance(messages, list):
        return new_body
    image_count = 0
    skipped = 0
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            image_count += 1
            image_url = part.get("image_url")
            if isinstance(image_url, str):
                url = image_url
                is_str = True
            elif isinstance(image_url, dict):
                url = image_url.get("url")
                is_str = False
            else:
                continue
            if not isinstance(url, str):
                continue
            if image_count > MAX_IMAGES_PER_REQUEST:
                if url.startswith("data:"):
                    skipped += 1
                continue
            new_url = _shrink_one(url)
            if new_url == url:
                continue
            if is_str:
                part["image_url"] = new_url
            else:
                image_url["url"] = new_url
    if skipped:
        logger.warning(
            "skipped shrinking %d images beyond MAX_IMAGES_PER_REQUEST=%d",
            skipped,
            MAX_IMAGES_PER_REQUEST,
        )
    return new_body
