from __future__ import annotations

import base64
import copy
import io
from typing import Any

from PIL import Image, ImageOps

MAX_LONG_EDGE = 1568
MAX_IMAGE_BYTES = 5 * 1024 * 1024

_FORMAT_MEDIA = {
    "PNG": "image/png",
    "JPEG": "image/jpeg",
    "WEBP": "image/webp",
    "GIF": "image/gif",
}


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
    # Try to open; leave unchanged on any failure.
    try:
        img = Image.open(io.BytesIO(raw))
    except Exception:  # noqa: BLE001
        return url
    try:
        w, h = img.size
        long_edge = max(w, h)
        if long_edge <= MAX_LONG_EDGE and len(raw) <= MAX_IMAGE_BYTES:
            return url
        img = ImageOps.exif_transpose(img)
        img.thumbnail((MAX_LONG_EDGE, MAX_LONG_EDGE))
        fmt = img.format
        if fmt is None:
            # Fall back to media type hint.
            mt = media_type.lower()
            if "png" in mt:
                fmt = "PNG"
            elif "jpeg" in mt or "jpg" in mt:
                fmt = "JPEG"
            elif "webp" in mt:
                fmt = "WEBP"
            elif "gif" in mt:
                fmt = "GIF"
        if fmt not in _FORMAT_MEDIA:
            return url
        out = io.BytesIO()
        if fmt == "JPEG" and img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.save(out, format=fmt)
        new_bytes = out.getvalue()
        new_media = _FORMAT_MEDIA[fmt]
        if len(new_bytes) > MAX_IMAGE_BYTES:
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


def shrink_images(body: dict) -> dict:
    new_body = copy.deepcopy(body)
    messages = new_body.get("messages")
    if not isinstance(messages, list):
        return new_body
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
                is_str = True
            elif isinstance(image_url, dict):
                url = image_url.get("url")
                is_str = False
            else:
                continue
            if not isinstance(url, str):
                continue
            new_url = _shrink_one(url)
            if new_url == url:
                continue
            if is_str:
                part["image_url"] = new_url
            else:
                image_url["url"] = new_url
    return new_body
