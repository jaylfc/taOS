from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.office_docs import VALID_KINDS, OfficeDocStore

router = APIRouter()


def _get_store(request: Request) -> OfficeDocStore:
    return request.app.state.office_docs


def _validate_kind(kind: Any) -> str | None:
    if not isinstance(kind, str) or kind not in VALID_KINDS:
        return None
    return kind


def _validate_title(title: Any) -> str | None:
    if not isinstance(title, str) or not title.strip():
        return None
    return title.strip()


@router.post("/api/office/docs")
async def create_doc(request: Request):
    body = await request.json()
    kind = _validate_kind(body.get("kind"))
    if kind is None:
        return JSONResponse({"error": "kind must be one of write, calc, db, slides"}, status_code=400)
    title = _validate_title(body.get("title"))
    if title is None:
        return JSONResponse({"error": "title is required"}, status_code=400)
    content = body.get("content", "")
    if not isinstance(content, str):
        return JSONResponse({"error": "content must be a string"}, status_code=400)

    store = _get_store(request)
    doc = await store.create(kind=kind, title=title, content=content)
    return doc


@router.get("/api/office/docs")
async def list_docs(request: Request):
    store = _get_store(request)
    return await store.list()


@router.get("/api/office/docs/{doc_id}")
async def get_doc(request: Request, doc_id: str):
    store = _get_store(request)
    doc = await store.get(doc_id)
    if doc is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return doc


@router.put("/api/office/docs/{doc_id}")
async def update_doc(request: Request, doc_id: str):
    body = await request.json()
    store = _get_store(request)

    existing = await store.get(doc_id)
    if existing is None:
        return JSONResponse({"error": "not found"}, status_code=404)

    kind = None
    if "kind" in body:
        kind = _validate_kind(body.get("kind"))
        if kind is None:
            return JSONResponse({"error": "kind must be one of write, calc, db, slides"}, status_code=400)

    title_raw = body.get("title", existing["title"])
    title = _validate_title(title_raw)
    if title is None:
        return JSONResponse({"error": "title is required"}, status_code=400)

    content = body.get("content", existing["content"])
    if not isinstance(content, str):
        return JSONResponse({"error": "content must be a string"}, status_code=400)

    doc = await store.update(doc_id=doc_id, title=title, content=content, kind=kind)
    return doc


@router.delete("/api/office/docs/{doc_id}")
async def delete_doc(request: Request, doc_id: str):
    store = _get_store(request)
    deleted = await store.delete(doc_id)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": "deleted", "id": doc_id}
