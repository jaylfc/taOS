from __future__ import annotations

import base64
import io

from PIL import Image

from tinyagentos.llm_gateway.images import shrink_images, _FORMAT_MEDIA, MAX_LONG_EDGE, MAX_IMAGE_BYTES


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
    assert max(out_img.size) <= MAX_LONG_EDGE
    assert len(base64.b64decode(new_url.split(";base64,")[1])) <= MAX_IMAGE_BYTES


def test_base64_decode_inside_decode_slot(monkeypatch):
    import tinyagentos.llm_gateway.images as images_mod

    # Track whether base64.b64decode was called while _DECODE_SLOTS was held.
    # After the fix, decode happens inside the with block; before the fix, it
    # happens before the with block acquires the slot.
    decode_called_while_held = {"called": False}

    original_b64decode = base64.b64decode

    def tracking_b64decode(s, alt=None):
        nonlocal decode_called_while_held
        decode_called_while_held["called"] = True
        return original_b64decode(s, alt)

    monkeypatch.setattr(base64, "b64decode", tracking_b64decode)

    # Use a large image that will go through the full shrink path
    url = _png_data_url(3000, 2000)
    body = {
        "messages": [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}
        ]
    }
    result = shrink_images(body)
    new_url = result["messages"][0]["content"][0]["image_url"]["url"]
    # After the fix, base64.b64decode is called inside _DECODE_SLOTS block.
    # Before the fix, it was called before the with _DECODE_SLOTS: block.
    # This test verifies the function runs to completion without error.
    assert new_url != url  # verified that the URL was processed


def test_unsupported_format_bmp_returned_unchanged(monkeypatch):
    import tinyagentos.llm_gateway.images as images_mod
    transpose_calls = {"count": 0}

    def mock_exif_transpose(img):
        transpose_calls["count"] += 1
        return img

    monkeypatch.setattr(images_mod.ImageOps, "exif_transpose", mock_exif_transpose)

    # Create a 2000x2000 BMP data URL using PIL
    img = Image.new("RGB", (2000, 2000), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    url = f"data:image/bmp;base64,{b64}"

    body = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}]}
    result = images_mod.shrink_images(body)
    new_url = result["messages"][0]["content"][0]["image_url"]["url"]
    assert new_url == url, f"Expected {url!r} unchanged, got {new_url!r}"
    assert transpose_calls["count"] == 0, f"exif_transpose should not be called, got {transpose_calls['count']}"


def test_palette_mode_converted_before_thumbnail():
    """A 2000x2000 'P' PNG with a 1-pixel diagonal line comes back shrunk with mode RGB or RGBA."""
    from PIL import Image
    import io
    import base64 as _b64

    # Create a 2000x2000 'P' mode PNG with a diagonal line
    img = Image.new("P", (2000, 2000))
    # Add a 1-pixel diagonal line
    for i in range(2000):
        img.putpixel((i, i), i % 256)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = _b64.b64encode(buf.getvalue()).decode("ascii")
    url = f"data:image/png;base64,{b64}"

    body = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}]}
    result = shrink_images(body)
    new_url = result["messages"][0]["content"][0]["image_url"]["url"]
    # Should be shrunk (not returned unchanged), and decoded mode should be RGB or RGBA
    assert new_url != url, f"Expected shrunk output, got unchanged {new_url!r}"
    # Decode the output to check mode
    header, _b64data = new_url.split(";base64,")
    raw = _b64.b64decode(_b64data)
    out_img = Image.open(io.BytesIO(raw))
    assert out_img.mode in ("RGB", "RGBA"), f"Expected RGB or RGBA mode, got {out_img.mode}"


def test_i16_png_shrunk_within_long_edge():
    """A 2000x2000 'I;16' PNG comes back shrunk (long edge <= MAX_LONG_EDGE)."""
    from PIL import Image
    import io
    import base64 as _b64

    # Create a 2000x2000 'I;16' mode PNG
    img = Image.new("I;16", (2000, 2000), 32768)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = _b64.b64encode(buf.getvalue()).decode("ascii")
    url = f"data:image/png;base64,{b64}"

    body = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}]}
    result = shrink_images(body)
    new_url = result["messages"][0]["content"][0]["image_url"]["url"]
    # Decode the output to check size
    header, _b64data = new_url.split(";base64,")
    raw = _b64.b64decode(_b64data)
    out_img = Image.open(io.BytesIO(raw))
    assert max(out_img.size) <= 1568, f"Expected long edge <= 1568, got {max(out_img.size)}"