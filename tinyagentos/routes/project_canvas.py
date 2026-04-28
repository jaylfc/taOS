"""REST API for per-project canvas boards.

See docs/superpowers/specs/2026-04-28-projects-canvas-board-design.md.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse, FileResponse, Response
from pydantic import BaseModel, Field

from tinyagentos.projects.canvas.store import CanvasPermissionError
from tinyagentos.projects.canvas.unfurl import fetch_link_metadata
from tinyagentos.projects.canvas.render import render_snapshot_png

logger = logging.getLogger(__name__)
router = APIRouter()


def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user and isinstance(user, dict) and "id" in user:
        return user["id"]
    return "system"


class CreateElementIn(BaseModel):
    kind: Literal["note", "link", "image", "user_shape"]
    x: float
    y: float
    w: float
    h: float
    rotation: float = 0
    z_index: int = 0
    payload: dict = Field(default_factory=dict)
    id: str | None = None


@router.get("/api/projects/{project_id}/canvas/elements")
async def list_canvas_elements(project_id: str, request: Request):
    cs = request.app.state.project_canvas_store
    elements = await cs.list_elements(project_id)
    return {"elements": elements}


@router.post("/api/projects/{project_id}/canvas/elements", status_code=201)
async def create_canvas_element(
    project_id: str, payload: CreateElementIn, request: Request,
):
    cs = request.app.state.project_canvas_store
    element = payload.model_dump()
    if element["kind"] == "link":
        url = (element.get("payload") or {}).get("url")
        if not url:
            return JSONResponse({"error": "link element requires payload.url"}, status_code=400)
        meta = await fetch_link_metadata(url)
        element["payload"] = meta
    try:
        new_el = await cs.add_element(
            project_id=project_id, element=element,
            author_kind="user", author_id=_user_id(request),
        )
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return {"element": new_el}


class PatchElementIn(BaseModel):
    x: float | None = None
    y: float | None = None
    w: float | None = None
    h: float | None = None
    rotation: float | None = None
    z_index: int | None = None
    payload: dict | None = None


@router.patch("/api/projects/{project_id}/canvas/elements/{element_id}")
async def update_canvas_element(
    project_id: str, element_id: str, payload: PatchElementIn, request: Request,
):
    cs = request.app.state.project_canvas_store
    patch = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        updated = await cs.update_element(
            project_id=project_id, element_id=element_id, patch=patch,
            author_kind="user", author_id=_user_id(request),
        )
    except CanvasPermissionError as e:
        return JSONResponse({"error": "permission_denied", "message": str(e)}, status_code=403)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    return {"element": updated}


@router.delete("/api/projects/{project_id}/canvas/elements/{element_id}", status_code=204)
async def delete_canvas_element(project_id: str, element_id: str, request: Request):
    cs = request.app.state.project_canvas_store
    try:
        await cs.delete_element(
            project_id=project_id, element_id=element_id,
            author_kind="user", author_id=_user_id(request),
        )
    except CanvasPermissionError as e:
        return JSONResponse({"error": "permission_denied", "message": str(e)}, status_code=403)
    return Response(status_code=204)
