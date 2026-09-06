from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.design_docs import DesignStore

router = APIRouter()

# Designs can embed image data URIs in `content`; cap the row so a single
# oversized upload can't bloat the SQLite database unboundedly.
MAX_CONTENT_BYTES = 5 * 1024 * 1024  # 5 MB


def _get_store(request: Request) -> DesignStore:
    return request.app.state.design_docs


def _validate_name(name: Any) -> str | None:
    if not isinstance(name, str) or not name.strip():
        return None
    return name.strip()


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


@router.post("/api/designs")
async def create_design(request: Request):
    body = await _parse_json(request)
    if isinstance(body, JSONResponse):
        return body
    name = _validate_name(body.get("name"))
    if name is None:
        return JSONResponse({"error": "name is required"}, status_code=400)
    content = body.get("content", "")
    content_error = _validate_content(content)
    if content_error is not None:
        return content_error

    store = _get_store(request)
    design = await store.create(name=name, content=content)
    return design


@router.get("/api/designs")
async def list_designs(request: Request):
    store = _get_store(request)
    return await store.list()


@router.get("/api/designs/{design_id}")
async def get_design(request: Request, design_id: str):
    store = _get_store(request)
    design = await store.get(design_id)
    if design is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return design


@router.put("/api/designs/{design_id}")
async def update_design(request: Request, design_id: str):
    body = await _parse_json(request)
    if isinstance(body, JSONResponse):
        return body
    store = _get_store(request)

    existing = await store.get(design_id)
    if existing is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    name_raw = body.get("name", existing["name"])
    name = _validate_name(name_raw)
    if name is None:
        return JSONResponse({"error": "name is required"}, status_code=400)

    content = body.get("content", existing["content"])
    content_error = _validate_content(content)
    if content_error is not None:
        return content_error

    design = await store.update(design_id=design_id, name=name, content=content)
    return design


@router.delete("/api/designs/{design_id}")
async def delete_design(request: Request, design_id: str):
    store = _get_store(request)
    deleted = await store.delete(design_id)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": "deleted", "id": design_id}
