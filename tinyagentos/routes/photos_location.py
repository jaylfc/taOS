"""Built-in Photos location for the Files app.

The phone camera saves to ``~/Pictures/taOS`` of the account the controller
runs as, which is outside every root the Files app lists. This module exposes
that standard Pictures folder as a built-in location (id ``photos``, label
``Photos``) so photos can be seen, shared and deleted from Files.

It is not a second file-browser mechanism: listing goes through the same
``_list_dir`` helper (via ``_list_photos_dir``, which only adds a tolerance for
an entry that cannot be stat'ed), and download / delete through the same
``_resolve_safe`` traversal guard and move-to-trash path, that the user and agent
workspace locations use (see tinyagentos/routes/user_workspace.py and
tinyagentos/routes/agent_workspace.py). Only read and delete are offered; the
camera folder is browsable and deletable but not a write target, the same way
the shared-folder locations are read-only in the Files app.

``GET /api/files/locations`` is how the Files app learns which built-in
locations exist on this device: an entry is returned only when its base
directory exists, so a device with no Pictures folder lists no Photos entry.

Who may reach it (tsk-j3gnef): there are no per-user photo folders yet, so the
folder belongs to the machine owner alone. ``_require_photos_access`` runs the
same owner-or-admin check the agent workspace routes run, against the primary
user instead of an agent's registry owner: the owner's session and admins reach
it, every other signed-in account gets 403 on ``/api/photos/*`` and no Photos
entry from ``/api/files/locations``. A local-token caller (a deployed agent --
the middleware maps that credential to the primary user) may list, download and
list the trash, and is refused on delete and restore: agents are read-only here.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

from tinyagentos.auth_context import CurrentUser, current_user, require_owner_or_admin
from tinyagentos.routes.user_workspace import _list_dir, _resolve_safe
from tinyagentos.workspace_trash import (
    TrashItemNotFound,
    TrashRestoreConflict,
    TrashRestoreOutsideRoot,
    get_trash_dir,
    list_trash_items,
    move_to_trash,
    restore_trash_item,
)

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

    The base is resolved because the Pictures folder may itself be a symlink
    (a camera roll moved to another disk leaves ``~/Pictures`` as a link).
    Everything this location builds is compared against, or made relative to,
    this base, so an unresolved base rejects items that are plainly inside it.
    """
    base = _photos_base(request)
    if not base.is_dir():
        return None, JSONResponse({"error": "Photos folder not found"}, status_code=404)
    return base.resolve(), None


def _get_photos_trash_dir(request: Request) -> Path:
    """Return the trash directory backing the Photos location, as the workspace
    locations do: one scope per browser kind, so a restore always lands back
    under the base the item was deleted from."""
    return get_trash_dir(request.app.state.config_path.parent, PHOTOS_TRASH_SCOPE)


def _local_token_caller(request: Request) -> tuple[bool, str | None]:
    """Whether this caller is a local token (a deployed agent or taosctl).

    Mirrors ``routes/memory.py::_local_token_caller``: ``AuthMiddleware`` stamps
    ``request.state.via == "local_token"`` for that credential, which is the only
    way these routes tell an agent apart from the human's browser session (both
    arrive as the primary user).
    """
    if getattr(request.state, "via", None) in ("local_token", "agent_token"):
        return True, getattr(request.state, "agent_name", None)
    return False, None


def _require_photos_access(request: Request) -> CurrentUser:
    """401 unless the caller is authenticated, 403 unless they may see the folder.

    Same shape as ``agent_workspace._require_agent_access``: ``current_user`` for
    the 401, then the shared ``require_owner_or_admin`` against the resource's
    owner. Here the owner is the machine's primary user, since the Pictures
    folder is one person's camera roll until photo folders are per-user. A local
    token is stamped with that user and marked admin by the middleware, so agents
    get through this check and are stopped from writing by
    ``_refuse_agent_writes`` below. With no primary user yet (pre-onboarding)
    ``require_owner_or_admin`` sees an owner of "" and stays closed.
    """
    user = current_user(request)
    if user.is_admin:
        return user
    auth_mgr = getattr(request.app.state, "auth", None)
    primary = auth_mgr.get_primary_user() if auth_mgr is not None else None
    require_owner_or_admin(user, primary["id"] if primary else "")
    return user


def _photos_visible(request: Request) -> bool:
    """Whether the caller may see a Photos entry in the location list.

    ``/api/files/locations`` is the sidebar's discovery call: it is shared with
    every account, so a caller the folder is not for gets the list without it
    rather than a 403 that would hide nothing else they can reach.
    """
    try:
        _require_photos_access(request)
    except HTTPException:
        return False
    return True


def _refuse_agent_writes(request: Request) -> None:
    """Agents are read-only in the camera folder: 403 on delete and restore."""
    is_local, _bound_agent = _local_token_caller(request)
    if is_local:
        raise HTTPException(
            status_code=403, detail="the Photos location is read-only for agents"
        )


def _list_photos_dir(base: Path, path: str) -> list[dict] | tuple[int, dict]:
    """``_list_dir`` for this location, but a broken entry cannot fail it.

    The shared helper calls ``Path.stat()`` on every entry, so one dangling
    symlink (the photo was moved off the phone while its link stayed, or a
    backup tool left a link to a path that is gone) turns the whole folder into
    a 500. That helper serves the workspace locations too, so the tolerance lives
    here: take the shared listing, and when it raises that way, list again
    skipping entries that cannot be stat'ed.
    """
    try:
        return _list_dir(base, path)
    except OSError as exc:
        logger.debug("photos listing retrying after %s", exc)

    if path:
        target = _resolve_safe(base, path)
        if target is None:
            return (400, {"error": "Invalid path"})
        if not target.exists() or not target.is_dir():
            return (404, {"error": "Directory not found"})
    else:
        target = base

    entries = []
    for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        try:
            stat = item.stat()
        except OSError:
            # A dangling symlink, or a file that vanished mid-listing: it is not
            # in the folder for the user to open, so it is not listed.
            continue
        rel = item.relative_to(base)
        entries.append({
            "name": item.name,
            "path": str(rel),
            "is_dir": item.is_dir(),
            "size": stat.st_size if item.is_file() else 0,
            "modified": stat.st_mtime,
        })
    return entries


@router.get("/api/files/locations")
async def api_list_locations(request: Request):
    """Built-in Files locations available on this device.

    Agent workspaces, projects and shared folders are discovered from their own
    endpoints; this returns the locations the controller owns directly, and only
    those whose base directory exists and that the caller is allowed to see.
    """
    locations = []
    if _photos_visible(request) and _photos_base(request).is_dir():
        locations.append({"id": PHOTOS_LOCATION_ID, "label": PHOTOS_LOCATION_LABEL})
    return locations


@router.get("/api/photos/files")
async def api_list_photos(request: Request, path: str = ""):
    """List the Pictures folder, optionally a subdirectory of it."""
    _require_photos_access(request)
    base, err = _photos_root(request)
    if err is not None:
        return err
    result = _list_photos_dir(base, path)
    if isinstance(result, tuple):
        status, body = result
        return JSONResponse(body, status_code=status)
    return result


@router.get("/api/photos/files/{file_path:path}")
async def api_get_photo(request: Request, file_path: str):
    """Stream a single photo -- used for thumbnails, previews and downloads."""
    _require_photos_access(request)
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
    """Move a photo to the trash, as the workspace locations do.

    The item lands in the ``photos`` trash scope, so the recycle bin's list and
    restore routes can put it back; nothing here unlinks it from disk.

    Only the parent directory is resolved, never the entry itself: an entry
    inside the folder that is a symlink (an alias for a photo) is the item the
    user sees, and moving its resolved target would trash the real photo and
    leave the alias dangling. A link pointing outside the folder or nowhere at
    all is therefore deleted as the link: the link moves, its target is never
    touched. Spellings that leave the base through the parent (``..``, a
    symlinked directory pointing outside) are still refused.
    """
    _require_photos_access(request)
    _refuse_agent_writes(request)
    base, err = _photos_root(request)
    if err is not None:
        return err
    name = Path(file_path).name
    if name in ("", ".", ".."):
        # "", "." and "sub/.." spell the folder itself or a parent, not a
        # deletable entry. Trashing the base would take the entire camera roll
        # (and the location with it), so refuse however it is spelled.
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    parent = _resolve_safe(base, str(Path(file_path).parent))
    if parent is None:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    target = parent / name
    # Judge existence by the directory entry: a dangling symlink is an entry the
    # user sees and may delete, and ``exists()`` follows the link and says no.
    if not (target.is_symlink() or target.exists()):
        return JSONResponse({"error": f"'{file_path}' not found"}, status_code=404)
    # The recorded path is the entry as it sits in the folder, never the raw
    # request string and never the target a symlink points at, so a restore
    # puts this entry back where the user deleted it from.
    original_path = str(target.relative_to(base.resolve()))
    await asyncio.to_thread(
        move_to_trash, _get_photos_trash_dir(request), target, original_path
    )
    return {"path": file_path, "status": "deleted"}


@router.get("/api/photos/trash")
async def api_list_photos_trash(request: Request):
    """List items in the Photos trash, newest-deleted first."""
    _require_photos_access(request)
    return {"items": list_trash_items(_get_photos_trash_dir(request))}


@router.post("/api/photos/trash/{item_id}/restore")
async def api_restore_photos_trash_item(request: Request, item_id: str):
    """Restore a trashed photo back to its original path in the Pictures folder."""
    _require_photos_access(request)
    _refuse_agent_writes(request)
    base, err = _photos_root(request)
    if err is not None:
        return err
    try:
        metadata = await asyncio.to_thread(
            restore_trash_item, _get_photos_trash_dir(request), base, item_id
        )
    except TrashItemNotFound:
        return JSONResponse({"error": "item not found"}, status_code=404)
    except TrashRestoreConflict:
        return JSONResponse(
            {"error": "a file already exists at the original path"}, status_code=409
        )
    except TrashRestoreOutsideRoot:
        return JSONResponse(
            {"error": "original path escapes the Photos folder"}, status_code=400
        )
    return {"status": "restored", "path": metadata["original_path"]}
