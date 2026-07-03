from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.web_sites import WebSiteStore

router = APIRouter()


def _get_store(request: Request) -> WebSiteStore:
    return request.app.state.web_sites


def _validate_title(title: Any) -> str | None:
    if not isinstance(title, str) or not title.strip():
        return None
    return title.strip()


@router.post("/api/web/sites")
async def create_site(request: Request):
    body = await request.json()
    title = _validate_title(body.get("title"))
    if title is None:
        return JSONResponse({"error": "title is required"}, status_code=400)
    content = body.get("content", "")
    if not isinstance(content, str):
        return JSONResponse({"error": "content must be a string"}, status_code=400)

    store = _get_store(request)
    site = await store.create(title=title, content=content)
    return site


@router.get("/api/web/sites")
async def list_sites(request: Request):
    store = _get_store(request)
    return await store.list()


@router.get("/api/web/sites/{site_id}")
async def get_site(request: Request, site_id: str):
    store = _get_store(request)
    site = await store.get(site_id)
    if site is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return site


@router.put("/api/web/sites/{site_id}")
async def update_site(request: Request, site_id: str):
    body = await request.json()
    store = _get_store(request)

    existing = await store.get(site_id)
    if existing is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    title_raw = body.get("title", existing["title"])
    title = _validate_title(title_raw)
    if title is None:
        return JSONResponse({"error": "title is required"}, status_code=400)

    content = body.get("content", existing["content"])
    if not isinstance(content, str):
        return JSONResponse({"error": "content must be a string"}, status_code=400)

    site = await store.update(site_id=site_id, title=title, content=content)
    return site


@router.delete("/api/web/sites/{site_id}")
async def delete_site(request: Request, site_id: str):
    store = _get_store(request)
    deleted = await store.delete(site_id)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": "deleted", "id": site_id}
