"""Session Catalog API routes — timeline browsing and pipeline control."""

from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from taosmd import SessionCatalog, CatalogPipeline

logger = logging.getLogger(__name__)

router = APIRouter(tags=["catalog"])


async def _get_catalog(request: Request) -> SessionCatalog:
    """Get or create session catalog from app state."""
    catalog = getattr(request.app.state, "session_catalog", None)
    if catalog is None:
        from pathlib import Path
        data_dir = getattr(request.app.state, "data_dir", Path("data"))
        catalog = SessionCatalog(
            db_path=data_dir / "session-catalog.db",
            archive_dir=data_dir / "archive",
            sessions_dir=data_dir / "sessions",
        )
        await catalog.init()
        request.app.state.session_catalog = catalog
    return catalog


@router.get("/api/memory/catalog/stats")
async def catalog_stats(request: Request):
    try:
        catalog = await _get_catalog(request)
        return await catalog.stats()
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/memory/catalog/date/{date}")
async def catalog_date(date: str, request: Request):
    catalog = await _get_catalog(request)
    sessions = await catalog.lookup_date(date)
    return sessions


@router.get("/api/memory/catalog/range")
async def catalog_range(start: str, end: str, request: Request):
    catalog = await _get_catalog(request)
    sessions = await catalog.lookup_range(start, end)
    return sessions


@router.get("/api/memory/catalog/search")
async def catalog_search(q: str, request: Request, limit: int = 20):
    catalog = await _get_catalog(request)
    return await catalog.search_topic(q, limit=limit)


@router.get("/api/memory/catalog/session/{session_id}")
async def catalog_session(session_id: int, request: Request):
    catalog = await _get_catalog(request)
    session = await catalog.get_session(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return session


@router.get("/api/memory/catalog/session/{session_id}/context")
async def catalog_session_context(session_id: int, request: Request):
    catalog = await _get_catalog(request)
    ctx = await catalog.get_session_context(session_id)
    if not ctx:
        raise HTTPException(404, "Session not found")
    return ctx


@router.get("/api/memory/catalog/recent")
async def catalog_recent(request: Request, limit: int = 20):
    catalog = await _get_catalog(request)
    return await catalog.recent(limit=limit)


@router.post("/api/memory/catalog/index")
async def catalog_index(request: Request):
    """Trigger indexing for a date or date range."""
    body = await request.json()
    date = body.get("date")
    start_date = body.get("start_date")
    end_date = body.get("end_date")
    force = body.get("force", False)

    from pathlib import Path
    data_dir = getattr(request.app.state, "data_dir", Path("data"))

    pipeline = CatalogPipeline(
        archive_dir=data_dir / "archive",
        sessions_dir=data_dir / "sessions",
        catalog_db=data_dir / "session-catalog.db",
        crystals_db=data_dir / "crystals.db",
        kg_db=data_dir / "knowledge-graph.db",
    )
    await pipeline.init()

    try:
        if date:
            result = await pipeline.index_day(date, force=force)
        elif start_date and end_date:
            result = await pipeline.index_range(start_date, end_date, force=force)
        else:
            result = await pipeline.index_yesterday()
        return result
    finally:
        await pipeline.close()


@router.post("/api/memory/catalog/rebuild")
async def catalog_rebuild(request: Request):
    """Rebuild entire catalog from archives."""
    from pathlib import Path
    data_dir = getattr(request.app.state, "data_dir", Path("data"))

    pipeline = CatalogPipeline(
        archive_dir=data_dir / "archive",
        sessions_dir=data_dir / "sessions",
        catalog_db=data_dir / "session-catalog.db",
        crystals_db=data_dir / "crystals.db",
        kg_db=data_dir / "knowledge-graph.db",
    )
    await pipeline.init()
    try:
        return await pipeline.rebuild()
    finally:
        await pipeline.close()
