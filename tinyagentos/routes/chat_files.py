from __future__ import annotations

import mimetypes
import os
import secrets
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

router = APIRouter()

_MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024  # 100 MB


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
    if source == "agent-workspace":
        if not slug or slug != owner:
            raise ValueError("slug must match path owner for agent-workspace")
    if source == "workspace":
        if owner != "user":
            raise ValueError("workspace source requires /workspaces/user/...")
    rel = parts[3] if len(parts) > 3 else ""
    root = (data_dir / "agent-workspaces" / owner).resolve()
    target = (root / rel).resolve()
    # Traversal check: target must be inside root.
    if not str(target).startswith(str(root) + os.sep) and target != root:
        raise ValueError("path traversal rejected")
    if not target.exists() or target.is_dir():
        raise ValueError("file not found")
    return target


@router.post("/api/chat/attachments/from-path")
async def attachment_from_path(body: dict, request: Request):
    """Server-side reference to a file in a workspace. Copies into
    chat-files/ and returns the attachment record."""
    vfs_path = (body or {}).get("path")
    source = (body or {}).get("source")
    slug = (body or {}).get("slug")
    if not vfs_path or source not in ("workspace", "agent-workspace"):
        return JSONResponse(
            {"error": "path and source in {workspace,agent-workspace} required"},
            status_code=400,
        )
    data_dir = request.app.state.data_dir
    try:
        src = _resolve_workspace_path(data_dir, source, slug, vfs_path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if src.stat().st_size > _MAX_ATTACHMENT_BYTES:
        return JSONResponse({"error": "file too large (100 MB max)"}, status_code=413)
    chat_files = data_dir / "chat-files"
    chat_files.mkdir(parents=True, exist_ok=True)
    stored_name = f"{secrets.token_hex(8)}-{src.name}"
    dest = chat_files / stored_name
    shutil.copy2(src, dest)
    mime, _ = mimetypes.guess_type(src.name)
    return JSONResponse({
        "filename": src.name,
        "mime_type": mime or "application/octet-stream",
        "size": src.stat().st_size,
        "url": f"/api/chat/files/{stored_name}",
        "source": source,
    }, status_code=200)


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


@router.get("/api/chat/files/{filename}")
async def serve_file(request: Request, filename: str):
    """Serve an uploaded chat file."""
    data_dir = request.app.state.data_dir
    file_path = data_dir / "chat-files" / filename
    if not file_path.exists() or not file_path.resolve().is_relative_to(
        (data_dir / "chat-files").resolve()
    ):
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(file_path)
