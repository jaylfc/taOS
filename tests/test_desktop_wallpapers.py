from __future__ import annotations

import io
from pathlib import Path

import pytest

from tinyagentos.stores.desktop_wallpapers import DesktopWallpapersStore


@pytest.fixture
def wallpapers_dir(tmp_data_dir):
    d = tmp_data_dir / "wallpapers"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def wallpapers_store(tmp_data_dir, wallpapers_dir):
    store = DesktopWallpapersStore(
        tmp_data_dir / "desktop_wallpapers.db",
        wallpapers_dir,
    )
    return store


# ---------------------------------------------------------------------------
# Store tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_init_creates_dir(wallpapers_store, wallpapers_dir):
    await wallpapers_store.init()
    assert wallpapers_dir.exists()
    assert wallpapers_store.db_path.exists()
    await wallpapers_store.close()


@pytest.mark.asyncio
async def test_store_add_and_list(wallpapers_store):
    await wallpapers_store.init()
    w1 = await wallpapers_store.add_wallpaper("test1", "abc.png", "image/png")
    w2 = await wallpapers_store.add_wallpaper("test2", "def.jpg", "image/jpeg")

    assert w1["id"]
    assert w1["label"] == "test1"
    assert w1["url"] == f"/api/desktop/wallpapers/{w1['id']}"

    wallpapers = await wallpapers_store.list_wallpapers()
    assert len(wallpapers) == 2
    # Newest first
    assert wallpapers[0]["id"] == w2["id"]

    await wallpapers_store.close()


@pytest.mark.asyncio
async def test_store_get_wallpaper(wallpapers_store):
    await wallpapers_store.init()
    w = await wallpapers_store.add_wallpaper("test", "img.png", "image/png")

    found = await wallpapers_store.get_wallpaper(w["id"])
    assert found is not None
    assert found["label"] == "test"

    missing = await wallpapers_store.get_wallpaper("nonexistent")
    assert missing is None

    await wallpapers_store.close()


@pytest.mark.asyncio
async def test_store_delete_wallpaper(wallpapers_store):
    await wallpapers_store.init()
    w = await wallpapers_store.add_wallpaper("test", "img.png", "image/png")

    deleted = await wallpapers_store.delete_wallpaper(w["id"])
    assert deleted is True

    # Not found after delete
    assert await wallpapers_store.get_wallpaper(w["id"]) is None

    # Delete non-existent returns False
    deleted2 = await wallpapers_store.delete_wallpaper("nonexistent")
    assert deleted2 is False

    await wallpapers_store.close()


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------


def _make_png() -> bytes:
    """Return a minimal valid PNG file (1×1 pixel)."""
    import struct
    import zlib

    def chunk(chunk_type: bytes, data: bytes) -> bytes:
        c = chunk_type + data
        crc = struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
        return struct.pack(">I", len(data)) + c + crc

    ihdr = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\xff\x00"))
        + chunk(b"IEND", b"")
    )


def _make_jpeg() -> bytes:
    """Return a minimal valid JPEG (1×1 pixel)."""
    return bytes([
        # SOI
        0xFF, 0xD8,
        # APP0
        0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01,
        0x01, 0x00, 0x00, 0x01, 0x00, 0x01, 0x00, 0x00,
        # DQT
        0xFF, 0xDB, 0x00, 0x43, 0x00,
    ] + [8] * 64 + [
        # SOF0
        0xFF, 0xC0, 0x00, 0x0B, 0x08, 0x00, 0x01, 0x00, 0x01,
        0x01, 0x01, 0x11, 0x00,
        # DHT
        0xFF, 0xC4, 0x00, 0x1F, 0x00, 0x00, 0x01, 0x05, 0x01, 0x01, 0x01,
        0x01, 0x01, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0A, 0x0B,
        # SOS
        0xFF, 0xDA, 0x00, 0x08, 0x01, 0x01, 0x00, 0x00, 0x3F, 0x00,
        0x00,
        # EOI
        0xFF, 0xD9,
    ])


@pytest.mark.asyncio
async def test_upload_png(client):
    png = _make_png()
    resp = await client.post(
        "/api/desktop/wallpapers",
        files={"file": ("test.png", io.BytesIO(png), "image/png")},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["id"]
    assert data["label"] == "test"
    assert data["url"] == f"/api/desktop/wallpapers/{data['id']}"


@pytest.mark.asyncio
async def test_upload_jpeg(client):
    jpeg = _make_jpeg()
    resp = await client.post(
        "/api/desktop/wallpapers",
        files={"file": ("photo.jpeg", io.BytesIO(jpeg), "image/jpeg")},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["label"] == "photo"


@pytest.mark.asyncio
async def test_upload_invalid_type(client):
    resp = await client.post(
        "/api/desktop/wallpapers",
        files={"file": ("doc.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
    )
    assert resp.status_code == 400
    assert "Unsupported" in resp.json()["error"]


@pytest.mark.asyncio
async def test_upload_too_large(client):
    # 11 MB of zeroes claiming to be PNG
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (11 * 1024 * 1024)
    resp = await client.post(
        "/api/desktop/wallpapers",
        files={"file": ("big.png", io.BytesIO(big), "image/png")},
    )
    assert resp.status_code == 400
    assert "too large" in resp.json()["error"]


@pytest.mark.asyncio
async def test_upload_not_an_image(client):
    resp = await client.post(
        "/api/desktop/wallpapers",
        files={"file": ("fake.png", io.BytesIO(b"hello world"), "image/png")},
    )
    assert resp.status_code == 400
    assert "valid image" in resp.json()["error"]


@pytest.mark.asyncio
async def test_list_wallpapers(client):
    # Upload two wallpapers
    png = _make_png()
    await client.post(
        "/api/desktop/wallpapers",
        files={"file": ("a.png", io.BytesIO(png), "image/png")},
    )
    await client.post(
        "/api/desktop/wallpapers",
        files={"file": ("b.png", io.BytesIO(png), "image/png")},
    )

    resp = await client.get("/api/desktop/wallpapers")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    for w in data:
        assert "id" in w
        assert "label" in w
        assert "url" in w
        assert "created_at" in w


@pytest.mark.asyncio
async def test_serve_wallpaper(client):
    png = _make_png()
    upload = await client.post(
        "/api/desktop/wallpapers",
        files={"file": ("test.png", io.BytesIO(png), "image/png")},
    )
    wp_id = upload.json()["id"]

    resp = await client.get(f"/api/desktop/wallpapers/{wp_id}")
    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "image/png"
    assert resp.content == png


@pytest.mark.asyncio
async def test_serve_missing_wallpaper(client):
    resp = await client.get("/api/desktop/wallpapers/nonexistent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_wallpaper(client):
    png = _make_png()
    upload = await client.post(
        "/api/desktop/wallpapers",
        files={"file": ("test.png", io.BytesIO(png), "image/png")},
    )
    wp_id = upload.json()["id"]

    resp = await client.delete(f"/api/desktop/wallpapers/{wp_id}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}

    # Verify it's gone
    resp2 = await client.get(f"/api/desktop/wallpapers/{wp_id}")
    assert resp2.status_code == 404

    # List should be empty
    list_resp = await client.get("/api/desktop/wallpapers")
    assert list_resp.json() == []


@pytest.mark.asyncio
async def test_delete_missing_wallpaper(client):
    resp = await client.delete("/api/desktop/wallpapers/nonexistent")
    assert resp.status_code == 404
