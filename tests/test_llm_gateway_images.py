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

    # Track whether _DECODE_SLOTS was held when base64.b64decode was called.
    slot = {"held": False, "calls": []}

    class TrackingSemaphore:
        def __enter__(self):
            slot["held"] = True
            return self

        def __exit__(self, *args):
            slot["held"] = False
            return False

    monkeypatch.setattr(images_mod, "_DECODE_SLOTS", TrackingSemaphore())

    original_b64decode = base64.b64decode

    def tracking_b64decode(s, alt=None):
        slot["calls"].append(slot["held"])
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

    # Restore original b64decode so _decode_data_url helper doesn't pollute calls
    monkeypatch.setattr(base64, "b64decode", original_b64decode)

    new_url = result["messages"][0]["content"][0]["image_url"]["url"]
    assert new_url != url, "URL should have been processed"
    assert slot["calls"], "base64.b64decode should have been called at least once"
    assert all(slot["calls"]), "Every base64.b64decode call must happen while _DECODE_SLOTS is held"


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


def test_large_encoded_url_bypasses_decode_slot(monkeypatch):
    """A data URL larger than MAX_ENCODED_CHARS is returned unchanged without taking a decode slot."""
    import tinyagentos.llm_gateway.images as images_mod

    monkeypatch.setattr(images_mod, "MAX_ENCODED_CHARS", 1000)

    class FailingSemaphore:
        def __enter__(self):
            raise AssertionError("_DECODE_SLOTS should not be acquired for oversized encoded URLs")

        def __exit__(self, *args):
            return False

    monkeypatch.setattr(images_mod, "_DECODE_SLOTS", FailingSemaphore())

    # Create a PNG data URL larger than 1000 chars
    url = _png_data_url(500, 500)  # This will be well over 1000 chars base64
    assert len(url) > 1000, "Test setup: URL must exceed patched MAX_ENCODED_CHARS"

    body = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}]}
    result = shrink_images(body)
    new_url = result["messages"][0]["content"][0]["image_url"]["url"]
    assert new_url == url, "Oversized encoded URL should be returned unchanged"


def test_transparent_gif_resized_saved_as_png(monkeypatch):
    """A transparent palette GIF resized beyond MAX_LONG_EDGE is saved as PNG to preserve alpha."""
    import tinyagentos.llm_gateway.images as images_mod
    from PIL import Image
    import io
    import base64 as _b64

    # Create a 2000x2000 'P' mode GIF with transparency
    img = Image.new("P", (2000, 2000))
    # Set a simple palette
    palette = []
    for i in range(256):
        palette.extend([i, i, i])
    img.putpalette(palette)
    # Draw something visible
    for i in range(0, 2000, 10):
        img.putpixel((i, i), 255)
    # Set transparency info (index 0 is transparent)
    img.info["transparency"] = 0

    buf = io.BytesIO()
    img.save(buf, format="GIF")
    b64 = _b64.b64encode(buf.getvalue()).decode("ascii")
    url = f"data:image/gif;base64,{b64}"

    body = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": url}}]}]}
    result = shrink_images(body)
    new_url = result["messages"][0]["content"][0]["image_url"]["url"]

    # Should be converted to PNG (not GIF) to preserve alpha
    assert new_url.startswith("data:image/png;base64,"), f"Expected PNG output, got {new_url[:50]}..."

    # Decode and verify mode and size
    header, _b64data = new_url.split(";base64,")
    raw = _b64.b64decode(_b64data)
    out_img = Image.open(io.BytesIO(raw))
    assert out_img.mode == "RGBA", f"Expected RGBA mode, got {out_img.mode}"
    assert max(out_img.size) <= images_mod.MAX_LONG_EDGE, f"Expected long edge <= {images_mod.MAX_LONG_EDGE}, got {max(out_img.size)}"