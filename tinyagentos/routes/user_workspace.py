from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)


async def _capture_file_activity(
    user_memory,
    *,
    filename: str,
    path: str,
    size: int,
    action: str,
    user_id: str = "user",
) -> None:
    """Fire-and-forget file activity capture. Never raises."""
    if not user_memory:
        return
    try:
        settings = await user_memory.get_settings(user_id)
        if not settings.get("capture_files"):
            return
        await user_memory.save_chunk(
            user_id,
            content=f"File {action}: {filename}",
            title=filename,
            collection="files",
            metadata={"path": path, "size": size, "action": action},
        )
    except Exception as e:  # pragma: no cover - best-effort
        logger.debug(f"file activity capture failed: {e}")

router = APIRouter()

USER_WORKSPACE_DIR_NAME = "workspace"


def _get_workspace_root(request: Request) -> Path:
    """Return the user workspace root, creating it on first access."""
    data_dir = request.app.state.config_path.parent
    ws = data_dir / USER_WORKSPACE_DIR_NAME
    ws.mkdir(parents=True, exist_ok=True)
    return ws


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
    """Shared listing logic used by the one-shot GET and the SSE watcher.

    Returns the entries list on success, or ``(status_code, error_dict)``
    on validation failure so the caller can build the right response.
    """
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
    """Stable hash-friendly string of a listing. Changes iff a file
    appears, disappears, or has its size / mtime modified."""
    parts = [f"{e['name']}:{int(e['modified'])}:{e['size']}" for e in entries]
    return "|".join(parts)


@router.get("/api/workspace/files")
async def api_list_files(request: Request, path: str = ""):
    """List files and directories in the user workspace, optionally in a subdirectory."""
    workspace = _get_workspace_root(request)
    result = _list_dir(workspace, path)
    if isinstance(result, tuple):
        status, body = result
        return JSONResponse(body, status_code=status)
    return result


@router.get("/api/workspace/files/watch")
async def api_watch_files(request: Request, path: str = "", interval: float = 1.0):
    """Server-sent events stream of directory contents — emits an event
    whenever the listing changes.

    Clients subscribe with an EventSource and patch their local state on
    each event. Uses stdlib polling (os.scandir via pathlib) at
    ``interval`` seconds — not inotify because we want zero new runtime
    dependencies and workspace directories are small enough (<1000 files)
    that polling is cheap. The SSE connection self-terminates if the
    client disconnects (FastAPI closes the underlying asyncio task)."""
    workspace = _get_workspace_root(request)
    interval = max(0.25, min(interval, 10.0))

    async def event_stream():
        # Send an immediate snapshot so the client's list is populated
        # before the first change fires.
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
            # Client disconnected, clean exit
            raise

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/workspace/files/upload")
async def api_upload_file(request: Request, path: str = "", file: UploadFile = File(...)):
    """Upload a file to the user workspace, optionally into a subdirectory."""
    workspace = _get_workspace_root(request)

    if path:
        target_dir = _resolve_safe(workspace, path)
        if target_dir is None:
            return JSONResponse({"error": "Invalid path"}, status_code=400)
        target_dir.mkdir(parents=True, exist_ok=True)
    else:
        target_dir = workspace

    filename = Path(file.filename).name  # strip any path component from filename
    dest = target_dir / filename
    content = await file.read()
    dest.write_bytes(content)
    rel = dest.relative_to(workspace)

    # Capture file activity into user memory (async, non-blocking)
    user_memory = getattr(request.app.state, "user_memory", None)
    if user_memory:
        asyncio.create_task(_capture_file_activity(
            user_memory,
            filename=filename,
            path=str(rel),
            size=len(content),
            action="upload",
        ))

    return {"name": filename, "path": str(rel), "size": len(content), "status": "uploaded"}


@router.post("/api/workspace/mkdir")
async def api_mkdir(request: Request, body: MkdirRequest):
    """Create a directory in the user workspace."""
    workspace = _get_workspace_root(request)

    if not body.path or not body.path.strip():
        return JSONResponse({"error": "path is required"}, status_code=400)

    target = _resolve_safe(workspace, body.path.strip())
    if target is None:
        return JSONResponse({"error": "Invalid path"}, status_code=400)

    target.mkdir(parents=True, exist_ok=True)
    rel = target.relative_to(workspace)
    return {"path": str(rel), "status": "created"}


class RenameRequest(BaseModel):
    src: str
    dst: str


@router.post("/api/workspace/rename")
async def api_rename(request: Request, body: RenameRequest):
    """Rename or move a file/directory within the user workspace."""
    workspace = _get_workspace_root(request)

    if not body.src.strip() or not body.dst.strip():
        return JSONResponse({"error": "src and dst are required"}, status_code=400)

    src = _resolve_safe(workspace, body.src.strip())
    dst = _resolve_safe(workspace, body.dst.strip())
    if src is None or dst is None:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    if not src.exists():
        return JSONResponse({"error": "Source not found"}, status_code=404)
    if dst.exists():
        return JSONResponse({"error": "Target already exists"}, status_code=409)

    dst.parent.mkdir(parents=True, exist_ok=True)
    src.rename(dst)
    return {"path": str(dst.relative_to(workspace)), "status": "renamed"}


@router.get("/api/workspace/files/{file_path:path}")
async def api_get_file(request: Request, file_path: str):
    """Stream a single file from the user workspace — used for thumbnails,
    previews, and direct downloads from the Files app."""
    workspace = _get_workspace_root(request)
    target = _resolve_safe(workspace, file_path)
    if target is None:
        return JSONResponse({"error": "Invalid path"}, status_code=400)
    if not target.exists() or not target.is_file():
        return JSONResponse({"error": f"'{file_path}' not found"}, status_code=404)
    return FileResponse(target, filename=target.name)


@router.delete("/api/workspace/files/{file_path:path}")
async def api_delete_file(request: Request, file_path: str):
    """Delete a file or directory from the user workspace."""
    workspace = _get_workspace_root(request)

    target = _resolve_safe(workspace, file_path)
    if target is None:
        return JSONResponse({"error": "Invalid path"}, status_code=400)

    if not target.exists():
        return JSONResponse({"error": f"'{file_path}' not found"}, status_code=404)

    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()

    return {"path": file_path, "status": "deleted"}


@router.get("/api/workspace/stats")
async def api_workspace_stats(request: Request):
    """Return total file count and total size of the user workspace."""
    workspace = _get_workspace_root(request)

    total_files = 0
    total_size = 0
    for item in workspace.rglob("*"):
        if item.is_file():
            total_files += 1
            total_size += item.stat().st_size

    return {
        "total_files": total_files,
        "total_size": total_size,
    }
