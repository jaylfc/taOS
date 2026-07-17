from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()

ALLOWED_MIME_TYPES = frozenset({"image/png", "image/jpeg", "image/webp"})
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


@router.post("/api/desktop/wallpapers")
async def upload_wallpaper(request: Request, file: UploadFile):
    """Accept a user-uploaded wallpaper image."""
    store = request.app.state.desktop_wallpapers

    if file.content_type not in ALLOWED_MIME_TYPES:
        return JSONResponse(
            {"error": f"Unsupported image type: {file.content_type}. "
                      "Allowed: image/png, image/jpeg, image/webp"},
            status_code=400,
        )

    data = await file.read()

    if len(data) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            {"error": f"Image too large ({len(data)} bytes). Max: {MAX_UPLOAD_BYTES} bytes (10 MB)"},
            status_code=400,
        )

    # Validate it's actually an image (check file magic)
    if not _is_image(data):
        return JSONResponse(
            {"error": "File does not appear to be a valid image"},
            status_code=400,
        )

    wp_id = uuid.uuid4().hex
    # Derive extension from the MIME type, not the original filename (security)
    ext = _ext_for_mime(file.content_type)
    filename = f"{wp_id}.{ext}"

    # Write to disk
    out_path = store.wallpapers_dir / filename
    out_path.write_bytes(data)

    label = (file.filename or "wallpaper").rsplit(".", 1)[0][:128]

    record = await store.add_wallpaper(
        label=label,
        filename=filename,
        mime_type=file.content_type,
    )
    return JSONResponse(record, status_code=201)


@router.get("/api/desktop/wallpapers")
async def list_wallpapers(request: Request):
    """List all user-uploaded wallpapers."""
    store = request.app.state.desktop_wallpapers
    wallpapers = await store.list_wallpapers()
    return JSONResponse(wallpapers)


@router.get("/api/desktop/wallpapers/{wp_id}")
async def serve_wallpaper(request: Request, wp_id: str):
    """Serve a user-uploaded wallpaper image."""
    store = request.app.state.desktop_wallpapers
    record = await store.get_wallpaper(wp_id)
    if record is None:
        return JSONResponse({"error": "Wallpaper not found"}, status_code=404)

    file_path = store.wallpapers_dir / record["filename"]
    if not file_path.is_file():
        return JSONResponse({"error": "Wallpaper file not found on disk"}, status_code=404)

    return FileResponse(
        file_path,
        media_type=record["mime_type"],
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.delete("/api/desktop/wallpapers/{wp_id}")
async def delete_wallpaper(request: Request, wp_id: str):
    """Delete a user-uploaded wallpaper."""
    store = request.app.state.desktop_wallpapers
    record = await store.get_wallpaper(wp_id)
    if record is None:
        return JSONResponse({"error": "Wallpaper not found"}, status_code=404)

    # Remove from disk
    file_path = store.wallpapers_dir / record["filename"]
    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        pass

    await store.delete_wallpaper(wp_id)
    return JSONResponse({"ok": True})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ext_for_mime(mime_type: str) -> str:
    _map = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
    return _map.get(mime_type, "bin")


def _is_image(data: bytes) -> bool:
    """Check file magic bytes for known image formats."""
    if len(data) < 4:
        return False
    # PNG: 89 50 4E 47
    if data[:4] == b"\x89PNG":
        return True
    # JPEG: FF D8 FF
    if data[:3] == b"\xff\xd8\xff":
        return True
    # WebP: 52 49 46 46 ... 57 45 42 50
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return True
    return False
