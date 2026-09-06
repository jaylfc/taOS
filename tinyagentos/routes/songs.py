from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.music_songs import SongStore

router = APIRouter()

# A song's `content` holds its serialized tracks/clips/notes JSON. Cap the row
# so a client can't store an arbitrary-size blob (unbounded SQLite growth /
# DoS). Same cap and posture as routes/web.py's MAX_CONTENT_BYTES.
MAX_CONTENT_BYTES = 5 * 1024 * 1024  # 5 MB

# --------------------------------------------------------------------------
# Music Studio song persistence (Phase 1) -- mirrors routes/office.py's CRUD
# pattern. Share-as-app (a self-contained .taosapp player bundle, mirroring
# routes/games.py's GET /{id}/package) is deferred: Phase 1 ships "Export" as
# a plain song-JSON download instead (musicstudio/songs-api.ts's
# exportSongFile), which needs no new backend endpoint. Revisit once there is
# a lightweight static Tone.js player shell worth embedding in a package.
# --------------------------------------------------------------------------


def _get_store(request: Request) -> SongStore:
    return request.app.state.song_store


def _validate_name(name: Any) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()


def _validate_content(content: Any) -> JSONResponse | None:
    """Return an error response if `content` is not an acceptably-sized string, else None."""
    if not isinstance(content, str):
        return JSONResponse({"error": "content must be a string"}, status_code=400)
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        return JSONResponse(
            {"error": f"content exceeds the {MAX_CONTENT_BYTES} byte limit"},
            status_code=413,
        )
    return None


@router.post("/api/songs")
async def create_song(request: Request):
    body = await request.json()
    name = _validate_name(body.get("name"))
    if name is None:
        return JSONResponse({"error": "name is required"}, status_code=400)
    content = body.get("content", "")
    err = _validate_content(content)
    if err is not None:
        return err

    store = _get_store(request)
    song = await store.create(name=name, content=content)
    return song


@router.get("/api/songs")
async def list_songs(request: Request):
    store = _get_store(request)
    return await store.list()


@router.get("/api/songs/{song_id}")
async def get_song(request: Request, song_id: str):
    store = _get_store(request)
    song = await store.get(song_id)
    if song is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return song


@router.put("/api/songs/{song_id}")
async def update_song(request: Request, song_id: str):
    body = await request.json()
    store = _get_store(request)

    existing = await store.get(song_id)
    if existing is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    name = _validate_name(body.get("name", existing["name"]))
    if name is None:
        return JSONResponse({"error": "name is required"}, status_code=400)

    content = body.get("content", existing["content"])
    err = _validate_content(content)
    if err is not None:
        return err

    song = await store.update(song_id=song_id, name=name, content=content)
    if song is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return song


@router.delete("/api/songs/{song_id}")
async def delete_song(request: Request, song_id: str):
    store = _get_store(request)
    deleted = await store.delete(song_id)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": "deleted", "id": song_id}
