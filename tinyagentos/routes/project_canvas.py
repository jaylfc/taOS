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


class PermissionIn(BaseModel):
    can_edit_canvas: bool


@router.get("/api/projects/{project_id}/canvas/snapshot.png")
async def get_canvas_png(project_id: str, request: Request):
    cs = request.app.state.project_canvas_store
    elements = await cs.list_elements(project_id)
    project = await request.app.state.project_store.get_project(project_id)
    if project is None:
        return JSONResponse({"error": "project not found"}, status_code=404)
    out = (
        request.app.state.projects_root
        / project["slug"] / "files" / "canvas"
    )
    out.mkdir(parents=True, exist_ok=True)
    target = out / "snapshot.png"
    render_snapshot_png(elements=elements, output_path=target)
    return FileResponse(target, media_type="image/png")


@router.get("/api/projects/{project_id}/canvas/snapshot.tldr")
async def get_canvas_tldr(project_id: str, request: Request):
    snap = request.app.state.canvas_snapshotter
    path = await snap.export_now(project_id)
    if path is None or not path.exists():
        return JSONResponse({"error": "project not found"}, status_code=404)
    return FileResponse(path, media_type="application/json")


@router.patch("/api/projects/{project_id}/canvas/permissions/{agent_id}")
async def set_canvas_permission(
    project_id: str, agent_id: str, payload: PermissionIn, request: Request,
):
    ps = request.app.state.project_store
    val = 1 if payload.can_edit_canvas else 0
    cur = await ps._db.execute(
        "UPDATE project_members SET can_edit_canvas = ? "
        "WHERE project_id = ? AND member_id = ?",
        (val, project_id, agent_id),
    )
    await ps._db.commit()
    if cur.rowcount == 0:
        return JSONResponse({"error": "member not found"}, status_code=404)
    broker = request.app.state.project_broker
    from tinyagentos.projects.events import ProjectEvent
    await broker.publish(
        project_id,
        ProjectEvent(
            kind="canvas.permission_changed",
            payload={"agent_id": agent_id, "can_edit_canvas": bool(val)},
        ),
    )
    return {"ok": True, "agent_id": agent_id, "can_edit_canvas": bool(val)}
