from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import secrets
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from filelock import FileLock

from tinyagentos.atomic_io import atomic_write_text
from tinyagentos.auth_context import current_user, require_agent_access
from tinyagentos.routes.project_files import _authorize_files_actor

router = APIRouter()

_MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024  # 100 MB
_REGISTRY_NAME = "chat-files-project-registry.json"


def _registry_path(data_dir: Path) -> Path:
    return data_dir / _REGISTRY_NAME


def _registry_lock_path(data_dir: Path) -> Path:
    return _registry_path(data_dir).with_suffix(".json.lock")


def _load_registry(data_dir: Path) -> dict:
    path = _registry_path(data_dir)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def _save_registry(data_dir: Path, registry: dict) -> None:
    path = _registry_path(data_dir)
    atomic_write_text(path, json.dumps(registry, indent=2))


def _file_identity(src: Path) -> str:
    h = hashlib.sha256()
    with src.open("rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _unique_project_dest(proj_files_root: Path, original_name: str) -> Path:
    dest = proj_files_root / original_name
    if not dest.exists():
        return dest
    stem = Path(original_name).stem
    suffix = Path(original_name).suffix
    counter = 1
    while True:
        candidate = proj_files_root / f"{stem}-{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


_WORKSPACE_OWNER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _resolve_workspace_path(data_dir: Path, source: str, slug: str | None, vfs_path: str) -> Path:
    """Resolve a VFS path like '/workspaces/user/foo.md' to an on-disk
    absolute path under data_dir/agent-workspaces/{slug-or-user}.
    Raises ValueError on traversal or bad shape.
    """
    if not vfs_path.startswith("/workspaces/"):
        raise ValueError("path must start with /workspaces/")
    parts = vfs_path.split("/", 3)  # ['', 'workspaces', '<slug>', 'rest...']
    if len(parts) < 3 or not parts[2]:
        raise ValueError("path missing slug")
    owner = parts[2]
    # The owner segment names a directory directly under agent-workspaces/,
    # so it must be a plain name: "." or ".." would make the root the
    # workspaces dir or the data dir itself, and every file in it readable.
    if not _WORKSPACE_OWNER_RE.fullmatch(owner) or owner in (".", ".."):
        raise ValueError("invalid workspace owner")
    if source == "agent-workspace":
        if not slug or slug != owner:
            raise ValueError("slug must match path owner for agent-workspace")
    if source == "workspace":
        if owner != "user":
            raise ValueError("workspace source requires /workspaces/user/...")
    rel = parts[3] if len(parts) > 3 else ""
    base = (data_dir / "agent-workspaces").resolve()
    root = (base / owner).resolve()
    target = (root / rel).resolve()
    # Traversal check: root must be a workspace under base, and target inside it.
    if root == base or not root.is_relative_to(base) or not target.is_relative_to(root):
        raise ValueError("path traversal rejected")
    if not target.exists() or target.is_dir():
        raise ValueError("file not found")
    return target


@router.post("/api/chat/attachments/from-path")
async def attachment_from_path(body: dict, request: Request):
    """Server-side reference to a file in a workspace. Copies into
    chat-files/ and returns the attachment record.

    When ``slug`` is provided the file is also copied into that project's
    files folder. The copy is idempotent: the registry is keyed on the
    source file's SHA-256 content hash, not on the per-request random
    stored_name, so re-sending the same file for the same project performs
    no second write. A different file that shares the original basename is
    landed under a non-colliding name so the existing project file is
    never silently overwritten.

    Bytes are copied into the project workspace (not referenced in place)
    because the source may be a transient workspace file that the user
    moves or deletes after the chat send; a copy in project Files keeps
    the attachment reachable from the project independently.
    """
    vfs_path = (body or {}).get("path")
    source = (body or {}).get("source")
    slug = (body or {}).get("slug")
    if not vfs_path or source not in ("workspace", "agent-workspace"):
        return JSONResponse(
            {"error": "path and source in {workspace,agent-workspace} required"},
            status_code=400,
        )
    data_dir = request.app.state.data_dir
    if source == "agent-workspace":
        # Same rule as the agent workspace routes: the agent's owner, an
        # admin, or the agent itself (a bound token reaches only its own).
        # Checked before the path is resolved, so a non-owner gets the same
        # 403 whether or not the file exists.
        if not slug:
            return JSONResponse(
                {"error": "slug must match path owner for agent-workspace"}, status_code=400
            )
        await require_agent_access(request, current_user(request), slug)
    try:
        src = _resolve_workspace_path(data_dir, source, slug, vfs_path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if slug:
        # Authorize the project write BEFORE copying anything, so a refused
        # request leaves no copy behind in chat-files/.
        auth = await _authorize_files_actor(request, slug, "write")
        if isinstance(auth, JSONResponse):
            return auth
    if src.stat().st_size > _MAX_ATTACHMENT_BYTES:
        return JSONResponse({"error": "file too large (100 MB max)"}, status_code=413)
    chat_files = data_dir / "chat-files"
    chat_files.mkdir(parents=True, exist_ok=True)
    stored_name = f"{secrets.token_hex(8)}-{src.name}"
    dest = chat_files / stored_name
    shutil.copy2(src, dest)
    mime, _ = mimetypes.guess_type(src.name)
    result = {
        "filename": src.name,
        "mime_type": mime or "application/octet-stream",
        "size": src.stat().st_size,
        "url": f"/api/chat/files/{stored_name}",
        "source": source,
    }
    if slug:
        identity = _file_identity(src)
        registry_path = _registry_path(data_dir)
        lock_path = _registry_lock_path(data_dir)
        with FileLock(str(lock_path), timeout=10):
            registry = _load_registry(data_dir)
            entry = registry.get(identity, {})
            registered_projects = entry.get("projects", [])
            if slug not in registered_projects:
                proj_files_root = request.app.state.projects_root / slug / "files"
                proj_files_root.mkdir(parents=True, exist_ok=True)
                dest_name = _unique_project_dest(proj_files_root, src.name)
                shutil.copy2(src, dest_name)
                entry["original_name"] = src.name
                entry["size"] = src.stat().st_size
                entry["projects"] = list(set(registered_projects + [slug]))
                registry[identity] = entry
                _save_registry(data_dir, registry)
        result["in_files_state"] = "registered"
        result["project_slug"] = slug
    return JSONResponse(result, status_code=200)


@router.post("/api/chat/upload")
async def upload_file(request: Request, file: UploadFile = File(...), channel_id: str = ""):
    """Upload a file attachment for use in chat messages."""
    data_dir = request.app.state.data_dir
    upload_dir = data_dir / "chat-files"
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_id = uuid.uuid4().hex[:12]
    ext = Path(file.filename).suffix if file.filename else ""
    stored_name = f"{file_id}{ext}"
    dest = upload_dir / stored_name
    content = await file.read()
    if len(content) > _MAX_ATTACHMENT_BYTES:
        return JSONResponse({"error": "file too large (100 MB max)"}, status_code=413)
    dest.write_bytes(content)

    attachment = {
        "id": file_id,
        "filename": file.filename or "unnamed",
        "content_type": file.content_type or "application/octet-stream",
        "size": len(content),
        "url": f"/api/chat/files/{stored_name}",
    }
    return attachment


def _safe_chat_file(data_dir: Path, filename: str) -> Path | None:
    candidate = data_dir / "chat-files" / filename
    if not candidate.exists() or not candidate.is_file():
        return None
    resolved = candidate.resolve()
    if not resolved.is_relative_to((data_dir / "chat-files").resolve()):
        return None
    return resolved


@router.get("/api/chat/files/{filename}")
async def serve_file(request: Request, filename: str):
    """Serve an uploaded chat file."""
    data_dir = request.app.state.data_dir
    safe_path = _safe_chat_file(data_dir, filename)
    if safe_path is None:
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(safe_path)


@router.get("/api/chat/attachments/resolve")
async def resolve_attachment(request: Request, filename: str, slug: str):
    """Resolve a chat file reference to its in-Files state.

    Returns a tri-state ``in_files_state``:
    - ``registered``: the file exists in chat-files and is registered in the
      named project's files.
    - ``not-registered``: the file exists in chat-files but is NOT registered
      in the named project.
    - ``unknown``: the stored filename does not resolve to anything in
      chat-files.

    Authorization follows the same existence-hiding 404 contract as
    ``tinyagentos.routes.project_files``: a caller who cannot see the project
    never learns it exists.
    """
    data_dir = request.app.state.data_dir
    auth = await _authorize_files_actor(request, slug, "read")
    if isinstance(auth, JSONResponse):
        return auth
    chat_file = _safe_chat_file(data_dir, filename)
    if chat_file is None:
        return JSONResponse({
            "filename": filename,
            "size": 0,
            "in_files_state": "unknown",
        }, status_code=200)
    identity = _file_identity(chat_file)
    registry = _load_registry(data_dir)
    entry = registry.get(identity, {})
    registered_projects = entry.get("projects", [])
    in_files_state = "registered" if slug in registered_projects else "not-registered"
    return JSONResponse({
        "filename": entry.get("original_name", filename),
        "size": entry.get("size", chat_file.stat().st_size),
        "in_files_state": in_files_state,
    }, status_code=200)
