"""S2b/S3: GET /api/device/v1/agents/{name}/avatar (device bearer, scope agents:read).

RED-FIRST: this module is written BEFORE the route exists so the first run
must FAIL with 404. The FAIL block is captured in RED-PROOF.md.
"""
from __future__ import annotations

import hashlib
import io
import os
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image

PLAIN = "http://testserver:6969"


def _bearer(tok):
    return {"Authorization": f"Bearer {tok}"}


def _client(app, base=PLAIN):
    return AsyncClient(transport=ASGITransport(app=app), base_url=base)


async def _device(app, user_id="u1", platform="ios", scopes=("agents:read",)):
    st = app.state.device_store
    d = await st.register(user_id=user_id, platform=platform)
    if scopes is not None:
        await st.set_scopes(d["device_id"], list(scopes))
    return d["scoped_token"]


def _make_image_bytes(w: int, h: int, color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    """Create a JPEG image bytes with the given size and color."""
    img = Image.new("RGB", (w, h), color)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()


def _make_red_image(size: int) -> bytes:
    """Create a pure red square JPEG for RGB565 native LE test."""
    return _make_image_bytes(size, size, (255, 0, 0))


def _make_green_image(size: int) -> bytes:
    """Create a pure green square JPEG for transparent-black test."""
    return _make_image_bytes(size, size, (0, 255, 0))


@pytest_asyncio.fixture
async def vapp(app, client):
    return app


# (a) test_avatar_96_is_lvgl9_rgb565a8
@pytest.mark.asyncio
async def test_avatar_96_is_lvgl9_rgb565a8(vapp, monkeypatch):
    from tinyagentos import agent_avatars as avatars

    app = vapp
    app.state.config.agents = [
        {"name": "avatar-agent", "framework": "openclaw", "user_id": "u1"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        slug = avatars._avatar_slug("avatar-agent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        # Use a 200x200 source to test centre-crop + resize to 96
        img_path.write_bytes(_make_image_bytes(200, 200, (128, 64, 32)))

        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        async with _client(app) as c:
            r = await c.get("/api/device/v1/agents/avatar-agent/avatar?size=96", headers=_bearer(tok))

    assert r.status_code == 200, r.text
    assert r.headers.get("content-type") == "application/x-taos-lvimg"

    body = r.content
    # 12-byte header + 96*96*3 pixels = 12 + 27648 = 27660
    assert len(body) == 12 + 96 * 96 * 3

    # Header: magic(1) cf(1) flags(1) reserved(1) w(2) h(2) stride(2) reserved(2) = 12 bytes
    magic, cf, flags, _res0, w, h, stride, _res1 = int.from_bytes(body[0:1], "little"), body[1], body[2], body[3], int.from_bytes(body[4:6], "little"), int.from_bytes(body[6:8], "little"), int.from_bytes(body[8:10], "little"), int.from_bytes(body[10:12], "little")
    assert magic == 0x19, f"magic={magic:#04x}"
    assert cf == 0x14, f"cf={cf:#04x} (LV_COLOR_FORMAT_RGB565A8)"
    assert flags == 0
    assert w == 96
    assert h == 96
    assert stride == 192  # w * 2


# (b) test_avatar_is_circle_masked
@pytest.mark.asyncio
async def test_avatar_is_circle_masked(vapp, monkeypatch):
    from tinyagentos import agent_avatars as avatars

    app = vapp
    app.state.config.agents = [
        {"name": "avatar-agent", "framework": "openclaw", "user_id": "u1"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        slug = avatars._avatar_slug("avatar-agent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        img_path.write_bytes(_make_image_bytes(200, 200, (128, 64, 32)))

        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        async with _client(app) as c:
            r = await c.get("/api/device/v1/agents/avatar-agent/avatar?size=96", headers=_bearer(tok))

    assert r.status_code == 200, r.text
    body = r.content
    w = h = 96
    header_size = 12
    rgb565_size = w * h * 2
    alpha_offset = header_size + rgb565_size

    # Alpha plane starts at offset 12 + 96*96*2
    alpha = body[alpha_offset:alpha_offset + w * h]

    # Corner (0,0) should be fully transparent (alpha=0)
    assert alpha[0] == 0, f"corner (0,0) alpha={alpha[0]}"

    # Center should be fully opaque (alpha=255)
    cx, cy = w // 2, h // 2
    center_idx = cy * w + cx
    assert alpha[center_idx] == 255, f"center alpha={alpha[center_idx]}"


# (b2) test_avatar_rgb565_native_little_endian
@pytest.mark.asyncio
async def test_avatar_rgb565_native_little_endian(vapp, monkeypatch):
    """Pure red (255,0,0) source -> centre pixel RGB565 bytes are 00 F8 (0xF800 LE), NOT F8 00."""
    from tinyagentos import agent_avatars as avatars

    app = vapp
    app.state.config.agents = [
        {"name": "red-agent", "framework": "openclaw", "user_id": "u1"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        slug = avatars._avatar_slug("red-agent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        img_path.write_bytes(_make_red_image(200))

        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        async with _client(app) as c:
            r = await c.get("/api/device/v1/agents/red-agent/avatar?size=96", headers=_bearer(tok))

    assert r.status_code == 200, r.text
    body = r.content
    w = h = 96
    header_size = 12
    rgb565_plane = body[header_size:header_size + w * h * 2]

    # Centre pixel at (48, 48) in 96x96
    cx, cy = w // 2, h // 2
    pixel_offset = (cy * w + cx) * 2
    b0, b1 = rgb565_plane[pixel_offset], rgb565_plane[pixel_offset + 1]

    # Pure red in RGB565 = 0xF800. Little-endian storage = bytes 00 F8
    assert (b0, b1) == (0x00, 0xF8), f"centre pixel bytes={b0:#04x} {b1:#04x}, expected 00 F8 (LE)"


# (b3) test_avatar_transparent_pixels_are_black
@pytest.mark.asyncio
async def test_avatar_transparent_pixels_are_black(vapp, monkeypatch):
    """Every pixel with alpha=0 has RGB565=0x0000 (black). Use non-black source so test can fail."""
    from tinyagentos import agent_avatars as avatars

    app = vapp
    app.state.config.agents = [
        {"name": "green-agent", "framework": "openclaw", "user_id": "u1"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        slug = avatars._avatar_slug("green-agent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        # Pure green source - corners will be masked to transparent
        img_path.write_bytes(_make_green_image(200))

        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        async with _client(app) as c:
            r = await c.get("/api/device/v1/agents/green-agent/avatar?size=96", headers=_bearer(tok))

    assert r.status_code == 200, r.text
    body = r.content
    w = h = 96
    header_size = 12
    rgb565_size = w * h * 2
    alpha_offset = header_size + rgb565_size

    alpha = body[alpha_offset:alpha_offset + w * h]
    rgb565 = body[header_size:header_size + rgb565_size]

    # Check all pixels where alpha == 0 have RGB565 == 0x0000
    for i in range(w * h):
        if alpha[i] == 0:
            b0 = rgb565[i * 2]
            b1 = rgb565[i * 2 + 1]
            assert b0 == 0x00 and b1 == 0x00, f"pixel {i} alpha=0 but RGB565={b0:#04x} {b1:#04x}, expected 00 00"


# (c) test_avatar_etag_and_304
@pytest.mark.asyncio
async def test_avatar_etag_and_304(vapp, monkeypatch):
    from tinyagentos import agent_avatars as avatars

    app = vapp
    app.state.config.agents = [
        {"name": "etag-agent", "framework": "openclaw", "user_id": "u1"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        slug = avatars._avatar_slug("etag-agent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        img_path.write_bytes(_make_image_bytes(200, 200, (100, 100, 100)))

        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        async with _client(app) as c:
            # First request
            r1 = await c.get("/api/device/v1/agents/etag-agent/avatar?size=96", headers=_bearer(tok))

            assert r1.status_code == 200, r1.text
            etag = r1.headers.get("etag")
            assert etag is not None
            # ETag format: "<hash>-96"
            assert etag.endswith('-96"'), f"ETag={etag}"

            # Second request with If-None-Match
            async with _client(app) as c:
                r2 = await c.get(
                    "/api/device/v1/agents/etag-agent/avatar?size=96",
                    headers={**_bearer(tok), "If-None-Match": etag},
                )

            assert r2.status_code == 304, r2.text
            assert r2.content == b""


# (d) test_avatar_cache_hit_and_invalidation
@pytest.mark.asyncio
async def test_avatar_cache_hit_and_invalidation(vapp, monkeypatch):
    from tinyagentos import agent_avatars as avatars

    app = vapp
    app.state.config.agents = [
        {"name": "cache-agent", "framework": "openclaw", "user_id": "u1"},
    ]

    call_count = {"convert": 0}

    # We'll patch the converter function once it exists
    # For now, just test the cache behavior via ETag changes
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        slug = avatars._avatar_slug("cache-agent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        img_path.write_bytes(_make_image_bytes(100, 100, (50, 50, 50)))

        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        async with _client(app) as c:
            r1 = await c.get("/api/device/v1/agents/cache-agent/avatar?size=96", headers=_bearer(tok))
        assert r1.status_code == 200
        etag1 = r1.headers.get("etag")

        async with _client(app) as c:
            r2 = await c.get("/api/device/v1/agents/cache-agent/avatar?size=96", headers=_bearer(tok))
        assert r2.status_code == 200
        etag2 = r2.headers.get("etag")
        assert etag1 == etag2, "ETag should be stable for same content"

        # Rewrite the source image
        img_path.write_bytes(_make_image_bytes(100, 100, (100, 100, 100)))

        async with _client(app) as c:
            r3 = await c.get("/api/device/v1/agents/cache-agent/avatar?size=96", headers=_bearer(tok))
        assert r3.status_code == 200
        etag3 = r3.headers.get("etag")
        assert etag3 != etag1, "ETag should change when source image changes"


# (e) test_avatar_size_whitelist
@pytest.mark.asyncio
async def test_avatar_size_whitelist(vapp, monkeypatch):
    from tinyagentos import agent_avatars as avatars

    app = vapp
    app.state.config.agents = [
        {"name": "size-agent", "framework": "openclaw", "user_id": "u1"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        slug = avatars._avatar_slug("size-agent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        img_path.write_bytes(_make_image_bytes(100, 100, (50, 50, 50)))

        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        # 45 is allowed
        async with _client(app) as c:
            r45 = await c.get("/api/device/v1/agents/size-agent/avatar?size=45", headers=_bearer(tok))
        assert r45.status_code == 200, f"size=45 failed: {r45.text}"

        # 96 is allowed
        async with _client(app) as c:
            r96 = await c.get("/api/device/v1/agents/size-agent/avatar?size=96", headers=_bearer(tok))
        assert r96.status_code == 200, f"size=96 failed: {r96.text}"

        # 64 is NOT allowed
        async with _client(app) as c:
            r64 = await c.get("/api/device/v1/agents/size-agent/avatar?size=64", headers=_bearer(tok))
        assert r64.status_code == 400, f"size=64 should be 400: {r64.text}"
        assert r64.json()["detail"]["error"] == "size_not_supported"

        # 100 is NOT allowed
        async with _client(app) as c:
            r100 = await c.get("/api/device/v1/agents/size-agent/avatar?size=100", headers=_bearer(tok))
        assert r100.status_code == 400, f"size=100 should be 400: {r100.text}"
        assert r100.json()["detail"]["error"] == "size_not_supported"


# (f) test_avatar_not_owned_or_missing_404
@pytest.mark.asyncio
async def test_avatar_not_owned_or_missing_404(vapp, monkeypatch):
    from tinyagentos import agent_avatars as avatars

    app = vapp
    # Owner 1 has an agent with avatar
    app.state.config.agents = [
        {"name": "owned-agent", "framework": "openclaw", "user_id": "owner-1"},
        {"name": "no-avatar-agent", "framework": "openclaw", "user_id": "owner-1"},
        {"name": "other-agent", "framework": "hermes", "user_id": "owner-2"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        slug1 = avatars._avatar_slug("owned-agent")
        img_path1 = Path(tmpdir) / f"{slug1}.jpg"
        img_path1.write_bytes(_make_image_bytes(100, 100, (80, 80, 80)))

        # No avatar for no-avatar-agent

        tok1 = await _device(app, user_id="owner-1", scopes=("agents:read",))
        tok2 = await _device(app, user_id="owner-2", scopes=("agents:read",))

        async with _client(app) as c:
            # Owner 1 requests their agent with avatar -> 200
            r_owned = await c.get("/api/device/v1/agents/owned-agent/avatar?size=96", headers=_bearer(tok1))
        assert r_owned.status_code == 200, f"owned agent failed: {r_owned.text}"

        async with _client(app) as c:
            # Owner 1 requests their agent WITHOUT avatar -> 404 avatar_not_found
            r_no_avatar = await c.get("/api/device/v1/agents/no-avatar-agent/avatar?size=96", headers=_bearer(tok1))
        assert r_no_avatar.status_code == 404, f"no avatar agent: {r_no_avatar.text}"
        assert r_no_avatar.json()["detail"]["error"] == "avatar_not_found"

        async with _client(app) as c:
            # Owner 2 requests owner 1's agent -> 404 (not 403, no leak)
            r_other = await c.get("/api/device/v1/agents/owned-agent/avatar?size=96", headers=_bearer(tok2))
        assert r_other.status_code == 404, f"other owner agent: {r_other.text}"

        async with _client(app) as c:
            # Unknown agent -> 404
            r_unknown = await c.get("/api/device/v1/agents/unknown-agent/avatar?size=96", headers=_bearer(tok1))
        assert r_unknown.status_code == 404, f"unknown agent: {r_unknown.text}"

        # Corrupt JPG -> 404, not 500
        slug_corrupt = avatars._avatar_slug("corrupt-agent")
        img_corrupt = Path(tmpdir) / f"{slug_corrupt}.jpg"
        img_corrupt.write_bytes(b"not a valid jpg")
        app.state.config.agents.append({"name": "corrupt-agent", "framework": "openclaw", "user_id": "owner-1"})

        async with _client(app) as c:
            r_corrupt = await c.get("/api/device/v1/agents/corrupt-agent/avatar?size=96", headers=_bearer(tok1))
        assert r_corrupt.status_code == 404, f"corrupt jpg should be 404 not 500: {r_corrupt.text}"
        assert r_corrupt.json()["detail"]["error"] == "avatar_not_found"


# (g) test_avatar_requires_agents_read
@pytest.mark.asyncio
async def test_avatar_requires_agents_read(vapp):
    app = vapp
    app.state.config.agents = [
        {"name": "scope-agent", "framework": "openclaw", "user_id": "u1"},
    ]

    # Device with chat:send but NOT agents:read
    tok = await _device(app, user_id="u1", scopes=("chat:send",))

    async with _client(app) as c:
        r = await c.get("/api/device/v1/agents/scope-agent/avatar?size=96", headers=_bearer(tok))

    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"error": "device_scope_missing", "scope": "agents:read"}


# (h) test_avatar_sibling_size_stays_cached
@pytest.mark.asyncio
async def test_avatar_sibling_size_stays_cached(vapp, monkeypatch):
    from tinyagentos import agent_avatars as avatars
    from tinyagentos.routes import device_avatar as da_mod

    app = vapp
    app.state.config.agents = [
        {"name": "sibling-agent", "framework": "openclaw", "user_id": "u1"},
    ]

    call_count = {"convert": 0}
    real_convert = da_mod._convert_to_lvgl9_rgb565a8

    def _counting_convert(source_path, size):
        call_count["convert"] += 1
        return real_convert(source_path, size)

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        monkeypatch.setattr(da_mod, "_convert_to_lvgl9_rgb565a8", _counting_convert)
        slug = avatars._avatar_slug("sibling-agent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        img_path.write_bytes(_make_image_bytes(200, 200, (128, 64, 32)))

        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        async with _client(app) as c:
            r45a = await c.get("/api/device/v1/agents/sibling-agent/avatar?size=45", headers=_bearer(tok))
        assert r45a.status_code == 200, r45a.text

        async with _client(app) as c:
            r96 = await c.get("/api/device/v1/agents/sibling-agent/avatar?size=96", headers=_bearer(tok))
        assert r96.status_code == 200, r96.text

        async with _client(app) as c:
            r45b = await c.get("/api/device/v1/agents/sibling-agent/avatar?size=45", headers=_bearer(tok))
        assert r45b.status_code == 200, r45b.text

    assert call_count["convert"] == 2, f"expected 2 conversions, got {call_count['convert']}"
    assert r45b.headers.get("etag") == r45a.headers.get("etag"), "third response should be served from cache (same ETag as first)"


# (i) test_avatar_old_hash_entry_removed
@pytest.mark.asyncio
async def test_avatar_old_hash_entry_removed(vapp, monkeypatch):
    from tinyagentos import agent_avatars as avatars
    from tinyagentos.routes import device_avatar as da_mod

    app = vapp
    app.state.config.agents = [
        {"name": "oldhash-agent", "framework": "openclaw", "user_id": "u1"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        slug = avatars._avatar_slug("oldhash-agent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        img_path.write_bytes(_make_image_bytes(100, 100, (50, 50, 50)))

        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        async with _client(app) as c:
            r1 = await c.get("/api/device/v1/agents/oldhash-agent/avatar?size=96", headers=_bearer(tok))
        assert r1.status_code == 200, r1.text
        etag1 = r1.headers.get("etag")

        # Rewrite the source image with different bytes -> new hash
        img_path.write_bytes(_make_image_bytes(100, 100, (100, 100, 100)))

        async with _client(app) as c:
            r2 = await c.get("/api/device/v1/agents/oldhash-agent/avatar?size=96", headers=_bearer(tok))
        assert r2.status_code == 200, r2.text
        etag2 = r2.headers.get("etag")
        assert etag2 != etag1, "ETag should change when source image changes"

        # Check the agent's cache directory holds exactly one .lvimg file carrying the new hash
        data_dir = app.state.config_path.parent
        cache_dir = da_mod._cache_dir(data_dir)
        agent_key = hashlib.sha256("oldhash-agent".encode()).hexdigest()[:16]
        agent_dir = cache_dir / agent_key
        lvimg_files = list(agent_dir.glob("*.lvimg")) if agent_dir.is_dir() else []
        assert len(lvimg_files) == 1, f"expected exactly 1 .lvimg in agent cache dir, found {len(lvimg_files)}: {lvimg_files}"
        assert etag2.strip('"') in lvimg_files[0].name, f"cache file {lvimg_files[0].name} should carry new hash {etag2}"


# (j) test_avatar_shared_agent_served
@pytest.mark.asyncio
async def test_avatar_shared_agent_served(vapp, monkeypatch):
    from tinyagentos import agent_avatars as avatars

    app = vapp
    app.state.config.agents = [
        {"name": "shared-agent", "framework": "openclaw", "user_id": ""},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        slug = avatars._avatar_slug("shared-agent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        img_path.write_bytes(_make_image_bytes(100, 100, (80, 80, 80)))

        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        async with _client(app) as c:
            r = await c.get("/api/device/v1/agents/shared-agent/avatar?size=96", headers=_bearer(tok))

    assert r.status_code == 200, f"shared agent should be served to device owner: {r.text}"
    assert r.headers.get("content-type") == "application/x-taos-lvimg"


# (k) test_avatar_if_none_match_multiple_values
@pytest.mark.asyncio
async def test_avatar_if_none_match_multiple_values(vapp, monkeypatch):
    from tinyagentos import agent_avatars as avatars

    app = vapp
    app.state.config.agents = [
        {"name": "multietag-agent", "framework": "openclaw", "user_id": "u1"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        slug = avatars._avatar_slug("multietag-agent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        img_path.write_bytes(_make_image_bytes(200, 200, (128, 64, 32)))

        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        async with _client(app) as c:
            r1 = await c.get("/api/device/v1/agents/multietag-agent/avatar?size=96", headers=_bearer(tok))

        assert r1.status_code == 200, r1.text
        etag = r1.headers.get("etag")
        assert etag is not None

        async with _client(app) as c:
            r2 = await c.get(
                "/api/device/v1/agents/multietag-agent/avatar?size=96",
                headers={**_bearer(tok), "If-None-Match": f'"other", {etag}'},
            )

        assert r2.status_code == 304, r2.text
        assert r2.content == b""


# (l) test_avatar_if_none_match_star
@pytest.mark.asyncio
async def test_avatar_if_none_match_star(vapp, monkeypatch):
    from tinyagentos import agent_avatars as avatars

    app = vapp
    app.state.config.agents = [
        {"name": "staragent", "framework": "openclaw", "user_id": "u1"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        slug = avatars._avatar_slug("staragent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        img_path.write_bytes(_make_image_bytes(200, 200, (128, 64, 32)))

        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        async with _client(app) as c:
            r = await c.get(
                "/api/device/v1/agents/staragent/avatar?size=96",
                headers={**_bearer(tok), "If-None-Match": "*"},
            )

        assert r.status_code == 304, r.text
        assert r.content == b""


# (m) test_avatar_if_none_match_weak
@pytest.mark.asyncio
async def test_avatar_if_none_match_weak(vapp, monkeypatch):
    from tinyagentos import agent_avatars as avatars

    app = vapp
    app.state.config.agents = [
        {"name": "weakagent", "framework": "openclaw", "user_id": "u1"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        slug = avatars._avatar_slug("weakagent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        img_path.write_bytes(_make_image_bytes(200, 200, (128, 64, 32)))

        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        async with _client(app) as c:
            r1 = await c.get("/api/device/v1/agents/weakagent/avatar?size=96", headers=_bearer(tok))

        assert r1.status_code == 200, r1.text
        etag = r1.headers.get("etag")
        assert etag is not None

        async with _client(app) as c:
            r2 = await c.get(
                "/api/device/v1/agents/weakagent/avatar?size=96",
                headers={**_bearer(tok), "If-None-Match": f"W/{etag}"},
            )

        assert r2.status_code == 304, r2.text
        assert r2.content == b""


# (n) test_avatar_truncated_cache_is_miss
@pytest.mark.asyncio
async def test_avatar_truncated_cache_is_miss(vapp, monkeypatch):
    from tinyagentos import agent_avatars as avatars
    from tinyagentos.routes import device_avatar as da_mod

    app = vapp
    app.state.config.agents = [
        {"name": "truncache-agent", "framework": "openclaw", "user_id": "u1"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        slug = avatars._avatar_slug("truncache-agent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        img_path.write_bytes(_make_image_bytes(200, 200, (128, 64, 32)))

        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        async with _client(app) as c:
            r1 = await c.get("/api/device/v1/agents/truncache-agent/avatar?size=96", headers=_bearer(tok))

        assert r1.status_code == 200, r1.text
        etag = r1.headers.get("etag")

        data_dir = app.state.config_path.parent
        agent_key = da_mod._agent_key("truncache-agent")
        ahash_from_etag = etag.strip('"').rsplit("-", 1)[0]
        cache_path = da_mod._cache_dir(data_dir) / agent_key / da_mod._cache_key(ahash_from_etag, 96)
        cache_path.write_bytes(b"\x00" * 5)
        assert cache_path.is_file(), f"cache file not at {cache_path}"
        assert cache_path.stat().st_size == 5, f"cache file size={cache_path.stat().st_size}"

        async with _client(app) as c:
            r2 = await c.get(
                "/api/device/v1/agents/truncache-agent/avatar?size=96",
                headers=_bearer(tok),
            )

        assert r2.status_code == 200, r2.text
        assert len(r2.content) == 12 + 96 * 96 * 3


# (o) test_cache_hit_with_wrong_header_is_regenerated
@pytest.mark.asyncio
async def test_cache_hit_with_wrong_header_is_regenerated(vapp, monkeypatch):
    from tinyagentos import agent_avatars as avatars
    from tinyagentos.routes import device_avatar as da_mod

    app = vapp
    app.state.config.agents = [
        {"name": "wrongheader-agent", "framework": "openclaw", "user_id": "u1"},
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setattr(avatars, "LOCK_AVATAR_DIR", tmpdir)
        slug = avatars._avatar_slug("wrongheader-agent")
        img_path = Path(tmpdir) / f"{slug}.jpg"
        img_path.write_bytes(_make_image_bytes(200, 200, (128, 64, 32)))

        tok = await _device(app, user_id="u1", scopes=("agents:read",))

        async with _client(app) as c:
            r1 = await c.get("/api/device/v1/agents/wrongheader-agent/avatar?size=96", headers=_bearer(tok))

        assert r1.status_code == 200, r1.text
        etag = r1.headers.get("etag")

        data_dir = app.state.config_path.parent
        agent_key = da_mod._agent_key("wrongheader-agent")
        ahash_from_etag = etag.strip('"').rsplit("-", 1)[0]
        cache_path = da_mod._cache_dir(data_dir) / agent_key / da_mod._cache_key(ahash_from_etag, 96)
        assert cache_path.is_file(), f"cache file not at {cache_path}"

        wrong_header = b"\x00" * 12
        original_data = cache_path.read_bytes()
        cache_path.write_bytes(wrong_header + original_data[12:])

        async with _client(app) as c:
            r2 = await c.get(
                "/api/device/v1/agents/wrongheader-agent/avatar?size=96",
                headers=_bearer(tok),
            )

        assert r2.status_code == 200, r2.text
        correct_header = r2.content[:12]
        assert correct_header == da_mod._lvimg_header(96)
