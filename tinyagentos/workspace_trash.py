"""Shared move-to-trash helper for host-side file browser routes.

The Files app lets a user browse three kinds of host-side folders: their own
workspace, an agent's workspace, and a project's files folder. All three used
to hard-delete on "delete" (``shutil.rmtree`` / ``Path.unlink``), bypassing
the Recycle Bin entirely — see the agent-container trash-cli integration in
``tinyagentos/routes/recycle.py``, which only ever sees files trashed *inside*
an agent's own container shell, never files removed via the Files app browser
on the host.

This module gives each of those three route files (``user_workspace.py``,
``agent_workspace.py``, ``project_files.py``) a tiny, dependency-free trash:
"delete" moves the item into a per-scope trash directory alongside a JSON
metadata sidecar (original relative path, deleted-at, whether it was a
directory), and the real filesystem delete only happens on an explicit purge
or empty-trash call. This matches taOS's "nothing is truly deleted" posture
without requiring a container / trash-cli dependency for host-side folders.
"""
from __future__ import annotations

import json
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

TRASH_DIRNAME = ".taos-trash"


def get_trash_dir(data_dir: Path, scope: str) -> Path:
    """Return the trash directory for a given scope, creating it on first use.

    ``scope`` namespaces trash by browser kind, e.g. "workspace",
    "agents/<agent_name>", "projects/<slug>" — so restoring an item always
    puts it back under the workspace root it came from.
    """
    trash_dir = data_dir / TRASH_DIRNAME / scope
    trash_dir.mkdir(parents=True, exist_ok=True)
    return trash_dir


def _meta_path(trash_dir: Path, item_id: str) -> Path:
    return trash_dir / f"{item_id}.json"


def _item_dir(trash_dir: Path, item_id: str) -> Path:
    return trash_dir / item_id


def move_to_trash(trash_dir: Path, target: Path, rel_path: str) -> dict:
    """Move *target* into the trash, writing a metadata sidecar.

    *rel_path* is the path relative to the owning workspace root (what the
    Files app displays / what restore needs to reconstruct the destination).
    Returns the metadata dict that was written.
    """
    item_id = uuid.uuid4().hex
    is_dir = target.is_dir()
    size_bytes = None
    if not is_dir:
        try:
            size_bytes = target.stat().st_size
        except OSError:
            size_bytes = None

    holder = _item_dir(trash_dir, item_id)
    holder.mkdir(parents=True, exist_ok=True)
    shutil.move(str(target), str(holder / target.name))

    metadata = {
        "id": item_id,
        "name": target.name,
        "original_path": rel_path,
        "is_dir": is_dir,
        "size_bytes": size_bytes,
        "deleted_at": datetime.now(timezone.utc).isoformat(),
    }
    _meta_path(trash_dir, item_id).write_text(json.dumps(metadata))
    return metadata


def list_trash_items(trash_dir: Path) -> list[dict]:
    """Return trash metadata, newest-deleted first."""
    items: list[dict] = []
    for meta_file in trash_dir.glob("*.json"):
        try:
            items.append(json.loads(meta_file.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    items.sort(key=lambda item: item.get("deleted_at", ""), reverse=True)
    return items


class TrashItemNotFound(Exception):
    """Raised when a trash item id has no matching metadata/contents."""


class TrashRestoreConflict(Exception):
    """Raised when restoring would overwrite an existing file/directory."""


def restore_trash_item(trash_dir: Path, workspace_root: Path, item_id: str) -> dict:
    """Move a trashed item back to its original path under *workspace_root*.

    Raises ``TrashItemNotFound`` if the id is unknown, or
    ``TrashRestoreConflict`` if something already exists at the destination.
    """
    meta_file = _meta_path(trash_dir, item_id)
    if not meta_file.exists():
        raise TrashItemNotFound(item_id)
    metadata = json.loads(meta_file.read_text())

    holder = _item_dir(trash_dir, item_id)
    source = holder / metadata["name"]
    if not source.exists():
        raise TrashItemNotFound(item_id)

    dest = workspace_root / metadata["original_path"]
    if dest.exists():
        raise TrashRestoreConflict(metadata["original_path"])

    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(dest))
    shutil.rmtree(holder, ignore_errors=True)
    meta_file.unlink(missing_ok=True)
    return metadata


def purge_trash_item(trash_dir: Path, item_id: str) -> bool:
    """Permanently delete one trashed item. Returns False if it doesn't exist."""
    meta_file = _meta_path(trash_dir, item_id)
    holder = _item_dir(trash_dir, item_id)
    if not meta_file.exists() and not holder.exists():
        return False
    shutil.rmtree(holder, ignore_errors=True)
    meta_file.unlink(missing_ok=True)
    return True


def empty_trash(trash_dir: Path) -> int:
    """Permanently delete every item in the trash. Returns the count removed."""
    count = 0
    for meta_file in trash_dir.glob("*.json"):
        item_id = meta_file.stem
        if purge_trash_item(trash_dir, item_id):
            count += 1
    return count
