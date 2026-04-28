"""Agent-facing handler functions for project canvases.

These are the in-process equivalents of the MCP tools described in
docs/superpowers/specs/2026-04-28-projects-canvas-board-design.md §4.
A real MCP server registration is a follow-up; for v1, agents that
run inside the same process can call these directly. Each function
returns either {"element": ...} / {"elements": ...} on success or
{"error": <code>, "message": <str>} on failure.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from tinyagentos.projects.canvas.store import (
    CanvasPermissionError,
    ProjectCanvasStore,
)
from tinyagentos.projects.canvas.unfurl import fetch_link_metadata
from tinyagentos.projects.canvas.render import render_snapshot_png
from tinyagentos.projects.canvas.snapshotter import CanvasSnapshotter


@dataclass
class CanvasToolContext:
    project_store: object
    canvas_store: ProjectCanvasStore
    snapshotter: CanvasSnapshotter
    data_root: Path


async def canvas_list_elements(ctx: CanvasToolContext, *, project_id: str) -> dict:
    elements = await ctx.canvas_store.list_elements(project_id)
    return {"elements": elements}


async def canvas_add_note(
    ctx: CanvasToolContext, *, project_id: str, agent_id: str,
    text: str, x: float, y: float, color: str = "yellow",
) -> dict:
    el = await ctx.canvas_store.add_element(
        project_id=project_id,
        author_kind="agent", author_id=agent_id,
        element={
            "kind": "note", "x": float(x), "y": float(y),
            "w": 200.0, "h": 100.0,
            "payload": {"text": text, "color": color, "font_size": 14},
        },
    )
    return {"element": el}


async def canvas_add_link(
    ctx: CanvasToolContext, *, project_id: str, agent_id: str,
    url: str, x: float, y: float,
) -> dict:
    meta = await fetch_link_metadata(url)
    el = await ctx.canvas_store.add_element(
        project_id=project_id,
        author_kind="agent", author_id=agent_id,
        element={
            "kind": "link", "x": float(x), "y": float(y),
            "w": 320.0, "h": 120.0, "payload": meta,
        },
    )
    return {"element": el}


async def canvas_add_image(
    ctx: CanvasToolContext, *, project_id: str, agent_id: str,
    file_id: str, x: float, y: float, alt: str = "",
) -> dict:
    el = await ctx.canvas_store.add_element(
        project_id=project_id,
        author_kind="agent", author_id=agent_id,
        element={
            "kind": "image", "x": float(x), "y": float(y),
            "w": 240.0, "h": 240.0,
            "payload": {"file_id": file_id, "alt": alt, "mime": "image/png"},
        },
    )
    return {"element": el}


async def canvas_update_element(
    ctx: CanvasToolContext, *, project_id: str, agent_id: str,
    element_id: str, patch: dict,
) -> dict:
    try:
        el = await ctx.canvas_store.update_element(
            project_id=project_id, element_id=element_id, patch=patch,
            author_kind="agent", author_id=agent_id,
        )
    except CanvasPermissionError:
        return {
            "error": "permission_denied",
            "message": (
                "This agent does not have edit permission on the canvas. "
                "Ask the user to enable it in project settings, or message "
                "them to make the change."
            ),
        }
    except ValueError as e:
        return {"error": "not_found", "message": str(e)}
    return {"element": el}


async def canvas_delete_element(
    ctx: CanvasToolContext, *, project_id: str, agent_id: str, element_id: str,
) -> dict:
    try:
        await ctx.canvas_store.delete_element(
            project_id=project_id, element_id=element_id,
            author_kind="agent", author_id=agent_id,
        )
    except CanvasPermissionError:
        return {
            "error": "permission_denied",
            "message": (
                "This agent does not have edit permission on the canvas. "
                "Ask the user to enable it in project settings, or message "
                "them to make the change."
            ),
        }
    return {"ok": True}


async def canvas_get_snapshot_png(
    ctx: CanvasToolContext, *, project_id: str,
) -> dict:
    project = await ctx.project_store.get_project(project_id)
    if project is None:
        return {"error": "not_found", "message": "project not found"}
    out_dir = ctx.data_root / project["slug"] / "files" / "canvas"
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"snapshot-{int(time.time())}.png"
    elements = await ctx.canvas_store.list_elements(project_id)
    render_snapshot_png(elements=elements, output_path=target)
    return {
        "file_path": str(target),
        "byte_size": target.stat().st_size,
    }
