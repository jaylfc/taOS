"""Library app routes — ingest, list, and manage library items.

POST /api/library/ingest  — accept file uploads or URL references
GET  /api/library/items   — list library items
GET  /api/library/items/{item_id} — item detail with artifacts
DELETE /api/library/items/{item_id} — remove item and its files
POST /api/library/items/{item_id}/reprocess — re-run the pipeline
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse

from tinyagentos.library_pipeline import run_pipeline
from tinyagentos.library_collections import handoff_to_collections
from tinyagentos.task_utils import _create_supervised_task

logger = logging.getLogger(__name__)

router = APIRouter()

LIBRARY_DIR_NAME = "library"

# Module-level background task tracking so unreferenced tasks are not
# garbage-collected when request.app.state._background_tasks is absent.
_background_tasks: set[asyncio.Task] = set()


def _track_background_task(coro) -> asyncio.Task:
    """Create a task, store it in ``_background_tasks``, and auto-discard on done."""
    task = asyncio.create_task(coro)

    def _on_done(t: asyncio.Task) -> None:
        _background_tasks.discard(t)

    task.add_done_callback(_on_done)
    _background_tasks.add(task)
    return task


# ---------------------------------------------------------------------------

def _library_dir_from_app(app) -> Path:
    """Return the library storage directory, creating it if needed."""
    data_dir = getattr(app.state, "data_dir", None)
    if data_dir:
        d = Path(data_dir) / LIBRARY_DIR_NAME
    else:
        d = Path(__file__).parent.parent.parent / "data" / LIBRARY_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _library_dir(request: Request) -> Path:
    return _library_dir_from_app(request.app)


async def _get_library_store(request: Request):
    """Get the LibraryStore from app.state (lazily initialised).

    Uses an asyncio.Lock to close the TOCTOU race where two concurrent
    requests both see ``store is None`` and both call ``LibraryStore(...)``.
    """
    store = getattr(request.app.state, "library_store", None)
    if store is not None:
        return store

    lock = getattr(request.app.state, "_library_store_init_lock", None)
    if lock is None:
        lock = asyncio.Lock()
        request.app.state._library_store_init_lock = lock

    async with lock:
        store = getattr(request.app.state, "library_store", None)
        if store is not None:
            return store

        from tinyagentos.library_store import LibraryStore

        data_dir = getattr(request.app.state, "data_dir", None)
        base = Path(data_dir) if data_dir else Path(__file__).parent.parent.parent / "data"
        store = LibraryStore(base / "library.db")
        await store.init()
        request.app.state.library_store = store
    return store


# ---------------------------------------------------------------------------
# Ingest
# ---------------------------------------------------------------------------


@router.post("/api/library/ingest")
async def ingest(
    request: Request,
    file: UploadFile | None = File(None),
    url: str | None = Form(None),
    title: str | None = Form(None),
):
    """Ingest a file or URL into the library.

    Accepts a file upload (multipart) or a URL string form field. At least one
    of ``file`` or ``url`` must be provided.

    Returns ``{item_id, status: \"pending\"}`` immediately — pipeline processing
    happens asynchronously in a background task.
    """
    store = await _get_library_store(request)
    storage_dir = _library_dir(request)

    if file and file.filename:
        # File upload
        kind = _detect_kind_from_filename(file.filename, file.content_type)
        file_dir = storage_dir / "files"
        file_dir.mkdir(parents=True, exist_ok=True)

        # Sanitise filename
        safe_name = _sanitise_filename(file.filename)
        dest = file_dir / f"{uuid.uuid4().hex[:8]}_{safe_name}"

        # Stream file in bounded chunks to avoid loading it entirely into
        # memory.  Reject uploads exceeding 100 MB with HTTP 413.
        MAX_SIZE = 100 * 1024 * 1024  # 100 MB
        if file.size and file.size > MAX_SIZE:
            return JSONResponse(
                {"error": "Payload Too Large"}, status_code=413,
            )

        size = 0
        with dest.open("wb") as f:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_SIZE:
                    dest.unlink(missing_ok=True)
                    return JSONResponse(
                        {"error": "Payload Too Large"}, status_code=413,
                    )
                f.write(chunk)

        item_id = await store.create_item(
            kind=kind,
            title=title or file.filename,
            storage_path=str(dest),
            size_bytes=size,
            source_url="",
        )
    elif url:
        # URL reference
        kind = _detect_kind_from_url(url)
        item_id = await store.create_item(
            kind=kind,
            source_url=url,
            title=title or url,
        )
    else:
        return JSONResponse(
            {"error": "Provide either 'file' (multipart upload) or 'url' (form field)."},
            status_code=400,
        )

    # Run pipeline in background
    task_set = getattr(request.app.state, "_background_tasks", None)
    coro = _ingest_task(request.app, item_id, store, storage_dir)
    if task_set is None:
        _track_background_task(coro)
    else:
        _create_supervised_task(coro, task_set)

    return JSONResponse({"item_id": item_id, "status": "pending"}, status_code=202)


async def _ingest_task(app, item_id: str, store, storage_dir: Path) -> None:
    """Background task: run pipeline + collections handoff for an item.

    Never raises — always leaves the item in a terminal status.
    """
    try:
        await run_pipeline(store, item_id, storage_dir)
    except Exception:
        logger.exception("Library ingest pipeline crashed for item %s", item_id)
        await store.update_item_status(item_id, "error")
        return

    # Collections handoff after successful pipeline
    try:
        collections_dir = storage_dir.parent / "collections"
        config = getattr(app.state, "config", None)
        taosmd_url = getattr(config, "memory_url", None) if config else None
        taosmd_admin_token = None
        secrets = getattr(app.state, "secrets", None)
        if secrets:
            secret = await secrets.get("taosmd-admin-token")
            if secret:
                taosmd_admin_token = secret["value"]
        indexed = await handoff_to_collections(
            store, item_id, collections_dir,
            taosmd_url=taosmd_url,
            taosmd_admin_token=taosmd_admin_token,
        )
        if indexed > 0:
            logger.info(
                "Collections handoff indexed %d file(s) for item %s",
                indexed, item_id,
            )
        else:
            logger.debug(
                "Collections handoff indexed 0 files for item %s "
                "(no text artifacts or taosmd unavailable)",
                item_id,
            )
    except Exception:
        logger.exception("Collections handoff failed for item %s", item_id)


def _sanitise_filename(name: str) -> str:
    """Strip path separators and null bytes from a filename."""
    name = name.replace("/", "_").replace("\\", "_").replace("\x00", "")
    # Also prevent double-dots for path traversal
    name = name.replace("..", "_")
    return name or "unnamed"


def _detect_kind_from_filename(filename: str, content_type: str | None = None) -> str:
    """Detect kind from filename and optional MIME type."""
    from tinyagentos.library_pipeline import detect_kind
    return detect_kind(file_path=filename, content_type=content_type or "")


def _detect_kind_from_url(url: str) -> str:
    """Detect kind from URL pattern."""
    from tinyagentos.library_pipeline import detect_kind
    return detect_kind(source_url=url)


# ---------------------------------------------------------------------------
# List / Get / Delete
# ---------------------------------------------------------------------------


@router.get("/api/library/items")
async def list_items(
    request: Request,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """List library items, optionally filtered by kind or status."""
    store = await _get_library_store(request)
    items = await store.list_items(kind=kind, status=status, limit=limit, offset=offset)
    return {"items": items, "count": len(items)}


@router.get("/api/library/items/{item_id}")
async def get_item(request: Request, item_id: str):
    """Get a library item with its artifacts."""
    store = await _get_library_store(request)
    item = await store.get_item(item_id)
    if not item:
        return JSONResponse({"error": f"Item {item_id!r} not found"}, status_code=404)

    artifacts = await store.get_artifacts(item_id)
    jobs = await store.get_item_jobs(item_id)
    return {"item": item, "artifacts": artifacts, "jobs": jobs}


@router.delete("/api/library/items/{item_id}")
async def delete_item(request: Request, item_id: str):
    """Delete a library item and its associated files."""
    store = await _get_library_store(request)
    item = await store.get_item(item_id)
    if not item:
        return JSONResponse({"error": f"Item {item_id!r} not found"}, status_code=404)

    # Remove on-disk files
    storage_path = item.get("storage_path", "")
    if storage_path and (p := Path(storage_path)).exists():
        try:
            p.unlink()
        except OSError:
            logger.warning("Failed to remove storage file %s for item %s",
                           storage_path, item_id)

    # Remove artifacts from disk
    artifacts = await store.get_artifacts(item_id)
    for art in artifacts:
        art_path = art.get("path", "")
        if art_path and (ap := Path(art_path)).exists():
            try:
                ap.unlink()
            except OSError:
                logger.warning("Failed to remove artifact %s for item %s",
                               art_path, item_id)

    # Remove collections folder
    storage_dir = _library_dir(request)
    item_collection_dir = storage_dir.parent / "collections" / item_id
    if item_collection_dir.exists():
        import shutil
        try:
            shutil.rmtree(item_collection_dir)
        except OSError:
            logger.warning("Failed to remove collection dir %s for item %s",
                           item_collection_dir, item_id)

    await store.delete_item(item_id)
    return {"status": "deleted", "item_id": item_id}


@router.post("/api/library/items/{item_id}/reprocess")
async def reprocess_item(request: Request, item_id: str):
    """Re-run the ingest pipeline for an existing item.

    Idempotent per (item, stage): old artifacts and their on-disk files are
    removed before the pipeline re-runs, so no duplicate rows or files accumulate.
    """
    store = await _get_library_store(request)
    item = await store.get_item(item_id)
    if not item:
        return JSONResponse({"error": f"Item {item_id!r} not found"}, status_code=404)

    # Atomic CAS: transition to pending only when NOT already pending/processing.
    # Beats the TOCTOU race — two concurrent reprocess requests cannot both
    # pass a separate read-then-write guard.
    if not await store.try_update_item_status(item_id, "pending",
                                               if_not_in=("pending", "processing")):
        return JSONResponse(
            {"error": "Item is currently being processed"}, status_code=409
        )

    # Delete old artifacts so reprocess is idempotent.
    # Guard: never unlink the user's original uploaded file (storage_path).
    storage_dir = _library_dir(request)
    old_artifacts = await store.get_artifacts(item_id)
    item_storage_path = item.get("storage_path", "")
    for art in old_artifacts:
        art_path = art.get("path", "")
        if art_path and art_path == item_storage_path:
            # This artifact records the source file — skip deletion.
            logger.debug("Skipping unlink of source file %s for item %s",
                         art_path, item_id)
        elif art_path and (ap := Path(art_path)).exists():
            try:
                ap.unlink()
            except OSError:
                logger.warning("Failed to remove artifact %s for item %s",
                               art_path, item_id)
        await store.delete_artifact(art["id"])

    # Status was already atomically set to pending by try_update_item_status above;
    # re-queue the pipeline now.  If scheduling fails, roll back to ready so the
    # item is not stuck pending forever.
    try:
        task_set = getattr(request.app.state, "_background_tasks", None)
        coro = _ingest_task(request.app, item_id, store, storage_dir)
        if task_set is None:
            _track_background_task(coro)
        else:
            _create_supervised_task(coro, task_set)
    except Exception:
        logger.exception("Failed to schedule reprocess for item %s", item_id)
        await store.update_item_status(item_id, "ready")
        return JSONResponse(
            {"error": "Failed to schedule reprocess"}, status_code=500,
        )

    return JSONResponse({"item_id": item_id, "status": "reprocessing"}, status_code=202)
