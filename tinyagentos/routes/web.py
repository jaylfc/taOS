from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.web_sites import WebSiteStore

router = APIRouter()

# Sites can embed image data URIs in `content`; cap the row so a single
# oversized upload can't bloat the SQLite database unboundedly.
MAX_CONTENT_BYTES = 5 * 1024 * 1024  # 5 MB


def _get_store(request: Request) -> WebSiteStore:
    return request.app.state.web_sites


def _validate_title(title: Any) -> str | None:
    if not isinstance(title, str) or not title.strip():
        return None
    return title.strip()


async def _parse_json(request: Request) -> dict | JSONResponse:
    # request.json() can raise JSONDecodeError on malformed input, but an empty
    # body or a wrong Content-Type can surface as a ValueError/UnicodeDecodeError
    # too. Treat all of them as a 400. A non-object JSON body (list, string,
    # number) is also rejected so downstream .get() access is always safe.
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    if not isinstance(body, dict):
        return JSONResponse({"error": "JSON body must be an object"}, status_code=400)
    return body


def _validate_content(content: Any) -> JSONResponse | None:
    """Returns an error response if `content` is invalid, else None."""
    if not isinstance(content, str):
        return JSONResponse({"error": "content must be a string"}, status_code=400)
    if len(content.encode("utf-8")) > MAX_CONTENT_BYTES:
        return JSONResponse(
            {"error": f"content exceeds the {MAX_CONTENT_BYTES} byte limit"},
            status_code=413,
        )
    return None


@router.post("/api/web/sites")
async def create_site(request: Request):
    body = await _parse_json(request)
    if isinstance(body, JSONResponse):
        return body
    title = _validate_title(body.get("title"))
    if title is None:
        return JSONResponse({"error": "title is required"}, status_code=400)
    content = body.get("content", "")
    content_error = _validate_content(content)
    if content_error is not None:
        return content_error

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
    body = await _parse_json(request)
    if isinstance(body, JSONResponse):
        return body
    store = _get_store(request)

    existing = await store.get(site_id)
    if existing is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    title_raw = body.get("title", existing["title"])
    title = _validate_title(title_raw)
    if title is None:
        return JSONResponse({"error": "title is required"}, status_code=400)

    content = body.get("content", existing["content"])
    content_error = _validate_content(content)
    if content_error is not None:
        return content_error

    site = await store.update(site_id=site_id, title=title, content=content)
    return site


@router.delete("/api/web/sites/{site_id}")
async def delete_site(request: Request, site_id: str):
    store = _get_store(request)
    deleted = await store.delete(site_id)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": "deleted", "id": site_id}
