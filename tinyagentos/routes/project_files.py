from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

from tinyagentos.workspace_trash import (
    TrashItemNotFound,
    TrashRestoreConflict,
    empty_trash,
    get_trash_dir,
    list_trash_items,
    move_to_trash,
    purge_trash_item,
    restore_trash_item,
)

router = APIRouter()


def _get_project_files_root(request: Request, slug: str) -> Path | None:
    """Return <projects_root>/<slug>/files, creating it on first access.
    Returns None if slug is empty / contains path separators."""
    if not slug or "/" in slug or "\\" in slug or slug in (".", ".."):
        return None
    root = request.app.state.projects_root / slug / "files"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _get_project_trash_dir(request: Request, slug: str) -> Path:
    """Return the trash directory backing one project's files, creating it
    on first access."""
    data_dir = Path(request.app.state.projects_root).parent
    return get_trash_dir(data_dir, f"projects/{slug}")


def _resolve_safe(workspace: Path, subpath: str) -> Path | None:
    """Resolve subpath relative to workspace, returning None if outside workspace."""
    try:
        resolved = (workspace / subpath).resolve()
        if resolved.is_relative_to(workspace.resolve()):
            return resolved
        return None
    except Exception:
        return None


class MkdirRequest(BaseModel):
    path: str


def _list_dir(workspace: Path, path: str) -> list[dict] | tuple[int, dict]:
    """Shared listing logic. Returns entries list on success, or (status, error) on failure."""
    if path:
        target = _resolve_safe(workspace, path)
        if target is None:
            return (400, {"error": "Invalid path"})
        if not target.exists() or not target.is_dir():
            return (404, {"error": "Directory not found"})
    else:
        target = workspace

    entries = []
    for item in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        stat = item.stat()
        rel = item.relative_to(workspace)
        entries.append({
            "name": item.name,
            "path": str(rel),
            "is_dir": item.is_dir(),
            "size": stat.st_size if item.is_file() else 0,
            "modified": stat.st_mtime,
        })
    return entries


def _dir_signature(entries: list[dict]) -> str:
    parts = [f"{e['name']}:{e['modified']}:{e['size']}" for e in entries]
    return "|".join(parts)


@router.get("/api/projects/{slug}/files")
async def api_project_list_files(request: Request, slug: str, path: str = ""):
    """List files in the project's files folder."""
    workspace = _get_project_files_root(request, slug)
    if workspace is None:
        return JSONResponse({"error": "Invalid slug"}, status_code=400)
    result = _list_dir(workspace, path)
    if isinstance(result, tuple):
        status, body = result
        return JSONResponse(body, status_code=status)
    return result


@router.get("/api/projects/{slug}/files/watch")
async def api_project_watch_files(request: Request, slug: str, path: str = "", interval: float = 1.0):
    """SSE watch stream for the project's files folder."""
    workspace = _get_project_files_root(request, slug)
    if workspace is None:
        return JSONResponse({"error": "Invalid slug"}, status_code=400)
    interval = max(0.25, min(interval, 10.0))

    async def event_stream():
        last_signature: str | None = None
        try:
            while True:
                if await request.is_disconnected():
                    break
                result = _list_dir(workspace, path)
                if isinstance(result, tuple):
                    status, body = result
                    yield f"event: error\ndata: {json.dumps(body)}\n\n"
                    break
                entries = result
                signature = _dir_signature(entries)
                if signature != last_signature:
                    last_signature = signature
                    yield f"data: {json.dumps(entries)}\n\n"
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            raise

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/projects/{slug}/files/upload")
async def api_project_upload_file(request: Request, slug: str, path: str = "", file: UploadFile = File(...)):
    """Upload a file to the project's files folder."""
    workspace = _get_project_files_root(request, slug)
    if workspace is None:
        return JSONResponse({"error": "Invalid slug"}, status_code=400)

    if path:
        target_dir = _resolve_safe(workspace, path)
        if target_dir is None:
            return JSONResponse({"error": "Invalid path"}, status_code=400)
        if target_dir.exists() and not target_dir.is_dir():
            return JSONResponse({"error": "Path conflicts with an existing file"}, status_code=400)
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        target_dir = workspace

    filename = Path(file.filename).name
    dest = target_dir / filename
    content = await file.read()
    dest.write_bytes(content)
    rel = dest.relative_to(workspace)
    return {"name": filename, "path": str(rel), "size": len(content), "status": "uploaded"}


@router.post("/api/projects/{slug}/mkdir")
async def api_project_mkdir(request: Request, slug: str, body: MkdirRequest):
    """Create a directory in the project's files folder."""
    workspace = _get_project_files_root(request, slug)
    if workspace is None:
        return JSONResponse({"error": "Invalid slug"}, status_code=400)

    if not body.path or not body.path.strip():
        return JSONResponse({"error": "path is required"}, status_code=400)

    target = _resolve_safe(workspace, body.path.strip())
    if target is None:
        return JSONResponse({"error": "Invalid path"}, status_code=400)

    if target.exists() and not target.is_dir():
        return JSONResponse({"error": "Path conflicts with an existing file"}, status_code=400)
    target.mkdir(parents=True, exist_ok=True)
    rel = target.relative_to(workspace)
    return {"path": str(rel), "status": "created"}


@router.get("/api/projects/{slug}/files/{file_path:path}")
async def api_project_get_file(request: Request, slug: str, file_path: str):
    """Stream a single file from the project's files folder."""
    workspace = _get_project_files_root(request, slug)
    if workspace is None:
        return JSONResponse({"error": "Invalid slug"}, status_code=400)
    target = _resolve_safe(workspace, file_path)
    if target is None:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    if not target.exists() or not target.is_file():
        return JSONResponse({"error": f"'{file_path}' not found"}, status_code=404)
    return FileResponse(target, filename=target.name)


@router.delete("/api/projects/{slug}/files/{file_path:path}")
async def api_project_delete_file(request: Request, slug: str, file_path: str):
    """Move a file or directory from the project's files folder to the trash.

    See ``tinyagentos/routes/user_workspace.py::api_delete_file`` for why —
    this mirrors the same move-to-trash behavior for project files.
    """
    workspace = _get_project_files_root(request, slug)
    if workspace is None:
        return JSONResponse({"error": "Invalid slug"}, status_code=400)

    target = _resolve_safe(workspace, file_path)
    if target is None:
        return JSONResponse({"error": "Invalid path"}, status_code=400)

    if not target.exists():
        return JSONResponse({"error": f"'{file_path}' not found"}, status_code=404)

    trash_dir = _get_project_trash_dir(request, slug)
    move_to_trash(trash_dir, target, file_path)

    return {"path": file_path, "status": "deleted"}


@router.get("/api/projects/{slug}/trash")
async def api_project_list_trash(request: Request, slug: str):
    """List items in a project's trash, newest-deleted first."""
    if _get_project_files_root(request, slug) is None:
        return JSONResponse({"error": "Invalid slug"}, status_code=400)
    trash_dir = _get_project_trash_dir(request, slug)
    return {"items": list_trash_items(trash_dir)}


@router.post("/api/projects/{slug}/trash/{item_id}/restore")
async def api_project_restore_trash_item(request: Request, slug: str, item_id: str):
    """Restore a trashed item back to its original path in the project's files folder."""
    workspace = _get_project_files_root(request, slug)
    if workspace is None:
        return JSONResponse({"error": "Invalid slug"}, status_code=400)
    trash_dir = _get_project_trash_dir(request, slug)
    try:
        metadata = restore_trash_item(trash_dir, workspace, item_id)
    except TrashItemNotFound:
        return JSONResponse({"error": "item not found"}, status_code=404)
    except TrashRestoreConflict:
        return JSONResponse(
            {"error": "a file already exists at the original path"}, status_code=409
        )
    return {"status": "restored", "path": metadata["original_path"]}


@router.delete("/api/projects/{slug}/trash/{item_id}")
async def api_project_purge_trash_item(request: Request, slug: str, item_id: str):
    """Permanently delete one item from a project's trash."""
    if _get_project_files_root(request, slug) is None:
        return JSONResponse({"error": "Invalid slug"}, status_code=400)
    trash_dir = _get_project_trash_dir(request, slug)
    try:
        found = purge_trash_item(trash_dir, item_id)
    except TrashItemNotFound:
        return JSONResponse({"error": "item not found"}, status_code=404)
    if not found:
        return JSONResponse({"error": "item not found"}, status_code=404)
    return {"status": "purged", "id": item_id}


@router.delete("/api/projects/{slug}/trash")
async def api_project_empty_trash(request: Request, slug: str):
    """Permanently delete every item in a project's trash."""
    if _get_project_files_root(request, slug) is None:
        return JSONResponse({"error": "Invalid slug"}, status_code=400)
    trash_dir = _get_project_trash_dir(request, slug)
    count = empty_trash(trash_dir)
    return {"status": "emptied", "count": count}


@router.get("/api/projects/{slug}/stats")
async def api_project_stats(request: Request, slug: str):
    """Return total file count and total size for the project's files folder."""
    workspace = _get_project_files_root(request, slug)
    if workspace is None:
        return JSONResponse({"error": "Invalid slug"}, status_code=400)

    total_files = 0
    total_size = 0
    for item in workspace.rglob("*"):
        if item.is_file():
            total_files += 1
            total_size += item.stat().st_size

    return {"total_files": total_files, "total_size": total_size}
