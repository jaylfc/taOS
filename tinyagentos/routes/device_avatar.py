"""GET /api/device/v1/agents/{name}/avatar (device bearer, scope agents:read).

Server-side conversion to LVGL 9 RGB565A8 (native little-endian, transparent=black)
circle-masked avatar. Cached per (avatar_hash, size). ETag/304 support.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import struct
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse
from PIL import Image, ImageDraw, ImageFilter

from tinyagentos.agent_avatars import avatar_hash, avatar_source_path
from tinyagentos.device_auth import device_scope
from tinyagentos.device_scopes import AGENTS_READ
from tinyagentos.atomic_io import atomic_write_bytes

router = APIRouter()

logger = logging.getLogger(__name__)

# Whitelisted sizes for Orb device
ALLOWED_SIZES = frozenset({45, 96})

# LVGL 9 color format constants
LV_COLOR_FORMAT_RGB565A8 = 0x14  # from lv_image_dsc.h
LV_IMAGE_HEADER_MAGIC = 0x19

# Cache directory name
CACHE_SUBDIR = "device-avatars"


def _agent_key(name: str) -> str:
    """Return a safe per-agent cache directory key (first 16 hex chars of sha256)."""
    return hashlib.sha256(name.encode()).hexdigest()[:16]


def _cache_dir(data_dir: Path) -> Path:
    """Return the cache directory for device avatars, creating it if needed."""
    cache_path = data_dir / "cache" / CACHE_SUBDIR
    cache_path.mkdir(parents=True, exist_ok=True)
    return cache_path


def _cache_key(avatar_hash_hex: str, size: int) -> str:
    """Cache key: <hash>-<size>.lvimg"""
    return f"{avatar_hash_hex}-{size}.lvimg"


def _etag_value(avatar_hash_hex: str, size: int) -> str:
    """ETag value: quoted "<hash>-<size>"""
    return f'"{avatar_hash_hex}-{size}"'


def _etag_matches(header: str | None, etag: str) -> bool:
    """Return True if the If-None-Match header matches the given ETag."""
    if not header:
        return False
    header = header.strip()
    if header == "*":
        return True
    for part in header.split(","):
        part = part.strip()
        if part.startswith("W/"):
            part = part[2:]
        if part == etag:
            return True
    return False


def _rgb_to_rgb565(r: int, g: int, b: int) -> int:
    """Convert 8-bit RGB to RGB565 (native little-endian value)."""
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def _make_circle_mask(size: int) -> Image.Image:
    """Create an anti-aliased circle mask at the given size.

    Renders at 4x and downsamples for smooth edges.
    """
    hi_size = size * 4
    mask_hi = Image.new("L", (hi_size, hi_size), 0)
    draw = ImageDraw.Draw(mask_hi)
    # Draw filled circle inset by 0.5px at high-res for clean edge
    draw.ellipse([2, 2, hi_size - 3, hi_size - 3], fill=255)
    # Downsample with LANCZOS for anti-aliasing
    mask = mask_hi.resize((size, size), Image.LANCZOS)
    return mask


def _convert_to_lvgl9_rgb565a8(source_path: Path, size: int) -> bytes:
    """Convert source image to LVGL 9 RGB565A8 format.

    Returns bytes: 12-byte header + RGB565 plane (stride*h) + A8 alpha plane (w*h).
    """
    # Open and convert to RGBA
    with Image.open(source_path) as img:
        img = img.convert("RGBA")

    # Centre-crop to square
    w, h = img.size
    if w != h:
        min_dim = min(w, h)
        left = (w - min_dim) // 2
        top = (h - min_dim) // 2
        img = img.crop((left, top, left + min_dim, top + min_dim))

    # Resize to target size with LANCZOS
    img = img.resize((size, size), Image.LANCZOS)

    # Create circle mask (anti-aliased via 4x downsample)
    mask = _make_circle_mask(size)

    # Apply mask to alpha channel
    rgba = img.load()
    for y in range(size):
        for x in range(size):
            alpha = mask.getpixel((x, y))
            r, g, b, _ = rgba[x, y]
            if alpha == 0:
                # Transparent pixels must be black (RGB565 = 0x0000)
                rgba[x, y] = (0, 0, 0, 0)
            else:
                rgba[x, y] = (r, g, b, alpha)

    # Build RGB565 plane and A8 alpha plane
    rgb565_plane = bytearray(size * size * 2)
    alpha_plane = bytearray(size * size)

    for y in range(size):
        for x in range(size):
            r, g, b, a = rgba[x, y]
            idx = y * size + x
            # RGB565 in native little-endian
            rgb565 = _rgb_to_rgb565(r, g, b)
            rgb565_plane[idx * 2] = rgb565 & 0xFF
            rgb565_plane[idx * 2 + 1] = (rgb565 >> 8) & 0xFF
            alpha_plane[idx] = a

    # 12-byte header compatible with lv_image_header_t (little-endian):
    #   uint8_t magic;        // 0x19
    #   uint8_t cf;           // LV_COLOR_FORMAT_RGB565A8 (0x14)
    #   uint8_t flags;        // 0
    #   uint8_t reserved0;    // 0
    #   uint16_t w;           // width
    #   uint16_t h;           // height
    #   uint16_t stride;      // w * 2 (RGB565 stride in bytes)
    #   uint16_t reserved1;   // 0
    header = struct.pack(
        "<BBBBHHHH",
        LV_IMAGE_HEADER_MAGIC,
        LV_COLOR_FORMAT_RGB565A8,
        0,  # flags
        0,  # reserved0
        size,  # w
        size,  # h
        size * 2,  # stride
        0,  # reserved1
    )

    return header + bytes(rgb565_plane) + bytes(alpha_plane)


@router.get("/api/device/v1/agents/{name}/avatar")
async def device_agent_avatar(
    name: str,
    request: Request,
    size: int,
    _device: dict = Depends(device_scope(AGENTS_READ)),
):
    """Serve agent avatar as LVGL 9 RGB565A8 image.

    Query params:
      - size: 45 or 96 (whitelisted)

    Returns:
      - 200: application/x-taos-lvimg body with 12-byte header + RGB565 + A8 planes
      - 304: If-None-Match matches ETag
      - 400: size not in whitelist
      - 404: agent not owned by device user, no avatar, or undecodable source
    """
    # Validate size
    if size not in ALLOWED_SIZES:
        raise HTTPException(
            status_code=400,
            detail={"error": "size_not_supported"},
        )

    # Get device owner
    owner_id = _device.get("user_id")
    if not owner_id:
        raise HTTPException(status_code=401, detail="device token required")

    # Verify agent belongs to this device's owner
    # We check via config.agents which is populated by the app
    config_agents = getattr(request.app.state, "config", None)
    if config_agents is None or not hasattr(config_agents, "agents"):
        raise HTTPException(status_code=404, detail={"error": "avatar_not_found"})

    agent_config = None
    for a in config_agents.agents:
        if a.get("name") == name:
            agent_config = a
            break

    if agent_config is None:
        # Unknown agent name -> 404 (never 403, no name leak)
        raise HTTPException(status_code=404, detail={"error": "avatar_not_found"})

    entry_uid = agent_config.get("user_id")
    if entry_uid and entry_uid != owner_id:
        # Agent belongs to different owner and is not shared -> 404 (not 403)
        raise HTTPException(status_code=404, detail={"error": "avatar_not_found"})

    # Get avatar hash (also validates source exists and is readable)
    ahash = await asyncio.to_thread(avatar_hash, name)
    if ahash is None:
        # No source image installed
        raise HTTPException(status_code=404, detail={"error": "avatar_not_found"})

    # ETag check
    etag = _etag_value(ahash, size)
    if_none_match = request.headers.get("if-none-match")
    if _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, max-age=86400"})

    # Check cache
    data_dir = request.app.state.config_path.parent
    agent_key = _agent_key(name)
    cache_path = _cache_dir(data_dir) / agent_key / _cache_key(ahash, size)

    if cache_path.is_file():
        try:
            data = cache_path.read_bytes()
        except OSError:
            data = None
        expected_len = 12 + size * size * 3
        if data is None or len(data) < 12 or data[0] != 0x19 or len(data) != expected_len:
            pass  # treat as cache miss, fall through
        else:
            return Response(
                content=data,
                media_type="application/x-taos-lvimg",
                headers={
                    "ETag": etag,
                    "Cache-Control": "private, max-age=86400",
                },
            )

    # Cache miss - convert
    source_path = avatar_source_path(name)
    if source_path is None:
        raise HTTPException(status_code=404, detail={"error": "avatar_not_found"})

    try:
        lvimg_data = await asyncio.to_thread(_convert_to_lvgl9_rgb565a8, source_path, size)
    except Exception as exc:  # noqa: BLE001
        # Undecodable source image -> 404 (never 500)
        logger.warning("Avatar conversion failed for agent %r: %s", name, exc)
        raise HTTPException(status_code=404, detail={"error": "avatar_not_found"})

    # Atomic write to cache
    agent_dir = cache_path.parent
    agent_dir.mkdir(parents=True, exist_ok=True)
    try:
        atomic_write_bytes(cache_path, lvimg_data)

        # Clean up stale cache entries for this agent (different sizes or old hashes)
        for old_file in agent_dir.glob("*.lvimg"):
            if not old_file.name.startswith(f"{ahash}-"):
                try:
                    old_file.unlink()
                except OSError:
                    pass
    except OSError as exc:
        logger.warning("Failed to write avatar cache for %r: %s", name, exc)
        # Still serve the image even if caching fails
        return Response(
            content=lvimg_data,
            media_type="application/x-taos-lvimg",
            headers={
                "ETag": etag,
                "Cache-Control": "private, max-age=86400",
            },
        )

    return Response(
        content=lvimg_data,
        media_type="application/x-taos-lvimg",
        headers={
            "ETag": etag,
            "Cache-Control": "private, max-age=86400",
        },
    )