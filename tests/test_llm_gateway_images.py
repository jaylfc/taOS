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


def test_oversized_pixels_passed_through(monkeypatch):
    import tinyagentos.llm_gateway.images as images_mod
    monkeypatch.setattr(images_mod, "MAX_SOURCE_PIXELS", 1000)
    url = _png_data_url(3000, 2000)
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}
        ]
    }
    result = shrink_images(body)
    new_url = result["messages"][0]["content"][0]["image_url"]["url"]
    assert new_url == url


def test_animated_gif_passed_through():
    # Create a 2-frame GIF larger than 1568px on long edge
    frame1 = Image.new("RGB", (2000, 1000), color=(255, 0, 0))
    frame2 = Image.new("RGB", (2000, 1000), color=(0, 255, 0))
    buf = io.BytesIO()
    frame1.save(buf, format="GIF", save_all=True, append_images=[frame2], duration=100, loop=0)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    url = f"data:image/gif;base64,{b64}"
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}
        ]
    }
    result = shrink_images(body)
    new_url = result["messages"][0]["content"][0]["image_url"]["url"]
    assert new_url == url


from tinyagentos.llm_gateway.images import MAX_IMAGES_PER_REQUEST, has_image_parts


def _body_with_images(urls):
    return {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": u}} for u in urls]}
        ]
    }


def test_per_request_image_cap_shrinks_first_eight():
    urls = [_png_data_url(2000, 1000) for _ in range(MAX_IMAGES_PER_REQUEST + 2)]
    body = _body_with_images(urls)
    result = shrink_images(body)
    parts = result["messages"][0]["content"]
    # first 8 shrunk to 1568 long edge
    for i in range(MAX_IMAGES_PER_REQUEST):
        new_url = parts[i]["image_url"]["url"]
        assert new_url != urls[i]
        _, img, _ = _decode_data_url(new_url)
        assert max(img.size) == 1568
    # last 2 byte-identical to input
    for i in range(MAX_IMAGES_PER_REQUEST, MAX_IMAGES_PER_REQUEST + 2):
        assert parts[i]["image_url"]["url"] == urls[i]


def test_has_image_parts_text_only_and_data_url():
    text_only = {"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}
    assert has_image_parts(text_only) is False
    img_body = {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]}
        ]
    }
    assert has_image_parts(img_body) is True


def test_jpeg_labelled_png_reencoded_as_jpeg():
    img = Image.new("RGB", (2000, 1000), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    url = f"data:image/png;base64,{b64}"
    body = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}]}
    result = shrink_images(body)
    new_url = result["messages"][0]["content"][0]["image_url"]["url"]
    media_type, out_img, _ = _decode_data_url(new_url)
    assert media_type == "image/jpeg"


def test_large_noisy_rgba_png_stays_png():
    import random
    size = 2000
    img = Image.new("RGBA", (size, size))
    pixels = [tuple(random.randrange(256) for _ in range(4)) for _ in range(size * size)]
    img.putdata(pixels)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    url = f"data:image/png;base64,{b64}"
    body = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}]}
    result = shrink_images(body)
    new_url = result["messages"][0]["content"][0]["image_url"]["url"]
    media_type, out_img, _ = _decode_data_url(new_url)
    assert media_type == "image/png"
    assert out_img.mode == "RGBA"
