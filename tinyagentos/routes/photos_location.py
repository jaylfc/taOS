"""Built-in Photos location for the Files app.

The phone camera saves to ``~/Pictures/taOS`` of the account the controller
runs as, which is outside every root the Files app lists. This module exposes
that standard Pictures folder as a built-in location (id ``photos``, label
``Photos``) so photos can be seen, shared and deleted from Files.

It is not a second file-browser mechanism: listing goes through the same
``_list_dir`` helper, and download / delete through the same ``_resolve_safe``
traversal guard and move-to-trash path, that the user and agent workspace
locations use (see tinyagentos/routes/user_workspace.py and
tinyagentos/routes/agent_workspace.py). Only read and delete are offered; the
camera folder is browsable and deletable but not a write target, the same way
the shared-folder locations are read-only in the Files app.

``GET /api/files/locations`` is how the Files app learns which built-in
locations exist on this device: an entry is returned only when its base
directory exists, so a device with no Pictures folder lists no Photos entry.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from tinyagentos.routes.user_workspace import _list_dir, _resolve_safe
from tinyagentos.workspace_trash import get_trash_dir, move_to_trash

logger = logging.getLogger(__name__)

router = APIRouter()

PHOTOS_LOCATION_ID = "photos"
PHOTOS_LOCATION_LABEL = "Photos"
# Overrides for the default ``~/$HOME/Pictures`` base, for tests and for
# installs that keep the camera roll elsewhere.
PHOTOS_DIR_ENV = "TAOS_PHOTOS_DIR"
PHOTOS_TRASH_SCOPE = "photos"


def _photos_base(request: Request) -> Path:
    """Base directory of the Photos location.

    The standard Pictures folder of the account the controller runs as. The
    ``server.photos_dir`` config key wins over the TAOS_PHOTOS_DIR environment
    variable; both exist so tests (and an unusual layout) can point the
    location somewhere else without moving a real Pictures folder.
    """
    config = getattr(request.app.state, "config", None)
    configured = ""
    if config is not None:
        server = getattr(config, "server", None) or {}
        configured = str(server.get("photos_dir") or "")
    configured = configured or os.environ.get(PHOTOS_DIR_ENV, "")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / "Pictures"


def _photos_root(request: Request) -> tuple[Path | None, JSONResponse | None]:
    """Return ``(base, None)`` when the location exists on this device.

    Mirrors ``agent_workspace._resolve_workspace``: on failure the caller
    returns the error response as-is. A missing Pictures folder is a 404
    rather than an empty listing so the UI can tell "no such location" apart
    from "nothing in it yet".
    """
    base = _photos_base(request)
    if not base.is_dir():
        return None, JSONResponse({"error": "Photos folder not found"}, status_code=404)
    return base, None


@router.get("/api/files/locations")
async def api_list_locations(request: Request):
    """Built-in Files locations available on this device.

    Agent workspaces, projects and shared folders are discovered from their own
    endpoints; this returns the locations the controller owns directly, and only
    those whose base directory exists.
    """
    locations = []
    if _photos_base(request).is_dir():
        locations.append({"id": PHOTOS_LOCATION_ID, "label": PHOTOS_LOCATION_LABEL})
    return locations


@router.get("/api/photos/files")
async def api_list_photos(request: Request, path: str = ""):
    """List the Pictures folder, optionally a subdirectory of it."""
    base, err = _photos_root(request)
    if err is not None:
        return err
    result = _list_dir(base, path)
    if isinstance(result, tuple):
        status, body = result
        return JSONResponse(body, status_code=status)
    return result


@router.get("/api/photos/files/{file_path:path}")
async def api_get_photo(request: Request, file_path: str):
    """Stream a single photo -- used for thumbnails, previews and downloads."""
    base, err = _photos_root(request)
    if err is not None:
        return err
    target = _resolve_safe(base, file_path)
    if target is None:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    if not target.exists() or not target.is_file():
        return JSONResponse({"error": f"'{file_path}' not found"}, status_code=404)
    return FileResponse(target, filename=target.name)


@router.delete("/api/photos/files/{file_path:path}")
async def api_delete_photo(request: Request, file_path: str):
    """Move a photo to the trash, as the workspace locations do."""
    base, err = _photos_root(request)
    if err is not None:
        return err
    target = _resolve_safe(base, file_path)
    if target is None:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    if not target.exists():
        return JSONResponse({"error": f"'{file_path}' not found"}, status_code=404)
    trash_dir = get_trash_dir(request.app.state.config_path.parent, PHOTOS_TRASH_SCOPE)
    move_to_trash(trash_dir, target, file_path)
    return {"path": file_path, "status": "deleted"}
