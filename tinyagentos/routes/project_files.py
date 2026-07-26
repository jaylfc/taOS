from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
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


_FILES_READ_SCOPE = "files_read"
_FILES_WRITE_SCOPE = "files_write"


async def _authorize_files_actor(
    request: Request, slug: str, mode: Literal["read", "write"]
) -> "tuple[str, str] | JSONResponse":
    """Resolve + authorize the actor for a project-files route.

    Mirrors ``_authorize_canvas_actor``: accepts EITHER a session owner/admin
    (human behavior unchanged) OR an approved agent's registry JWT holding the
    matching files scope bound to THIS project:

      * read mode  -> ``files_read`` grant on the project
      * write mode -> ``files_write`` grant on the project

    Returns ``(actor_kind, actor_id)`` on success, or a JSONResponse to return
    directly. A token bound to a DIFFERENT project (or an unknown slug)
    collapses into an existence-hiding 404 (never confirms the project exists).
    """
    ps = request.app.state.project_store
    project = await ps.get_project_by_slug(slug)
    uid = getattr(request.state, "user_id", None)
    if uid:
        # Session path: project visibility gate. A non-owner non-admin human
        # collapses into the SAME existence-hiding 404 the agent path uses.
        # An UNKNOWN slug is deliberately allowed through for session users:
        # the project files tree is slug-addressed and lazily created, which
        # test_list_unknown_slug_returns_empty documents as intended. The agent
        # path below is strict (unknown slug -> 404) because an agent must not
        # be able to address a project it was never granted.
        is_admin = bool(getattr(request.state, "is_admin", False))
        if project is not None and not is_admin and project.get("user_id") != uid:
            return JSONResponse({"error": "not found"}, status_code=404)
        return ("user", uid)
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.lower().startswith("bearer "):
        # Middleware normally 401s unauthenticated requests before the route
        # runs; a middleware-bypassing test context reaches here, so fall back
        # to a system actor (there is no real principal to attribute to).
        return ("user", "system")
    if project is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    from tinyagentos.agent_token_auth import (
        check_agent_scope_for_project,
        PROJECT_SCOPE_MISMATCH_DETAIL,
    )
    scope = _FILES_READ_SCOPE if mode == "read" else _FILES_WRITE_SCOPE
    try:
        cid = await check_agent_scope_for_project(request, scope, project["id"])
    except HTTPException as exc:
        if exc.status_code == 403 and exc.detail == PROJECT_SCOPE_MISMATCH_DETAIL:
            return JSONResponse({"error": "not found"}, status_code=404)
        raise
    if cid is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return ("agent", cid)


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
    auth = await _authorize_files_actor(request, slug, "read")
    if isinstance(auth, JSONResponse):
        return auth
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
    auth = await _authorize_files_actor(request, slug, "read")
    if isinstance(auth, JSONResponse):
        return auth
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
    auth = await _authorize_files_actor(request, slug, "write")
    if isinstance(auth, JSONResponse):
        return auth
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
    auth = await _authorize_files_actor(request, slug, "write")
    if isinstance(auth, JSONResponse):
        return auth
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
    auth = await _authorize_files_actor(request, slug, "read")
    if isinstance(auth, JSONResponse):
        return auth
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
    auth = await _authorize_files_actor(request, slug, "write")
    if isinstance(auth, JSONResponse):
        return auth
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
    auth = await _authorize_files_actor(request, slug, "read")
    if isinstance(auth, JSONResponse):
        return auth
    if _get_project_files_root(request, slug) is None:
        return JSONResponse({"error": "Invalid slug"}, status_code=400)
    trash_dir = _get_project_trash_dir(request, slug)
    return {"items": list_trash_items(trash_dir)}


@router.post("/api/projects/{slug}/trash/{item_id}/restore")
async def api_project_restore_trash_item(request: Request, slug: str, item_id: str):
    """Restore a trashed item back to its original path in the project's files folder."""
    auth = await _authorize_files_actor(request, slug, "write")
    if isinstance(auth, JSONResponse):
        return auth
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
    auth = await _authorize_files_actor(request, slug, "write")
    if isinstance(auth, JSONResponse):
        return auth
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
    auth = await _authorize_files_actor(request, slug, "write")
    if isinstance(auth, JSONResponse):
        return auth
    if _get_project_files_root(request, slug) is None:
        return JSONResponse({"error": "Invalid slug"}, status_code=400)
    trash_dir = _get_project_trash_dir(request, slug)
    count = empty_trash(trash_dir)
    return {"status": "emptied", "count": count}


@router.get("/api/projects/{slug}/stats")
async def api_project_stats(request: Request, slug: str):
    """Return total file count and total size for the project's files folder."""
    auth = await _authorize_files_actor(request, slug, "read")
    if isinstance(auth, JSONResponse):
        return auth
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
