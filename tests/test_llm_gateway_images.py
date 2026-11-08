from __future__ import annotations

import base64
import io

from PIL import Image

from tinyagentos.llm_gateway.images import shrink_images


def _png_data_url(width: int, height: int) -> str:
    img = Image.new("RGB", (width, height), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/png;base64,{b64}"


def _decode_data_url(url: str):
    header, b64 = url.split(";base64,", 1)
    media_type = header[len("data:"):]
    raw = base64.b64decode(b64)
    img = Image.open(io.BytesIO(raw))
    return media_type, img, raw


def test_large_png_shrunk_to_1568_long_edge():
    url = _png_data_url(3000, 2000)
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}
        ]
    }
    result = shrink_images(body)
    new_url = result["messages"][0]["content"][0]["image_url"]["url"]
    media_type, img, _ = _decode_data_url(new_url)
    assert max(img.size) == 1568
    assert media_type == "image/png"


def test_small_png_byte_identical():
    url = _png_data_url(100, 100)
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}
        ]
    }
    result = shrink_images(body)
    new_url = result["messages"][0]["content"][0]["image_url"]["url"]
    assert new_url == url


def test_https_url_and_text_part_unchanged():
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {"type": "image_url", "image_url": {"url": "https://example.com/img.png"}},
                ],
            }
        ]
    }
    result = shrink_images(body)
    assert result == body


def test_undecodable_data_url_unchanged_no_exception():
    url = "data:image/png;base64,!!!!notbase64!!!!"
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}
        ]
    }
    result = shrink_images(body)
    new_url = result["messages"][0]["content"][0]["image_url"]["url"]
    assert new_url == url


def test_input_body_not_mutated():
    url = _png_data_url(3000, 2000)
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}
        ]
    }
    original_url = body["messages"][0]["content"][0]["image_url"]["url"]
    _ = shrink_images(body)
    assert body["messages"][0]["content"][0]["image_url"]["url"] == original_url
    # still large
    _, img, _ = _decode_data_url(original_url)
    assert max(img.size) == 3000
