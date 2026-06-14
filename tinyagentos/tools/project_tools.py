"""Agent tools that build inside a project: create a project, add tasks to its
board, and place a generated image on its canvas.

These call the existing project/task/canvas stores IN-PROCESS (the same methods
the REST routes use), so their effects stream live to an open Projects app via
the existing project_event_broker SSE — the user watches the board fill and the
artwork land with no extra plumbing. Pairs with the desktop tools (open_app) so
the agent opens Projects, then builds in it visibly.
"""
from __future__ import annotations

import re

from fastapi import Request


def _user_id(request: Request) -> str | None:
    return getattr(request.state, "user_id", None) or None


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return s or "project"


async def execute_create_project(args: dict, request: Request) -> dict:
    name = (args or {}).get("name")
    if not name or not isinstance(name, str):
        return {"error": "create_project requires a 'name' string"}
    user_id = _user_id(request)
    if not user_id:
        return {"error": "no authenticated user"}
    store = request.app.state.project_store
    project = await store.create_project(
        name=name,
        slug=_slugify(name),
        created_by=user_id,
        description=(args or {}).get("description", "") or "",
        user_id=user_id,
    )
    return {"ok": True, "project_id": project["id"], "name": project["name"]}


async def execute_add_task(args: dict, request: Request) -> dict:
    project_id = (args or {}).get("project_id")
    title = (args or {}).get("title")
    if not project_id or not title:
        return {"error": "add_task requires 'project_id' and 'title'"}
    user_id = _user_id(request)
    if not user_id:
        return {"error": "no authenticated user"}
    store = request.app.state.project_task_store
    task = await store.create_task(project_id=project_id, title=title, created_by=user_id)
    return {"ok": True, "task_id": task["id"], "title": task["title"]}


async def execute_canvas_add_image(args: dict, request: Request) -> dict:
    project_id = (args or {}).get("project_id")
    file_id = (args or {}).get("file_id")
    if not project_id or not file_id:
        return {"error": "canvas_add_image requires 'project_id' and 'file_id'"}
    user_id = _user_id(request)
    if not user_id:
        return {"error": "no authenticated user"}
    store = request.app.state.project_canvas_store
    el = await store.add_element(
        project_id=project_id,
        element={
            "kind": "image",
            "x": float((args or {}).get("x", 80)),
            "y": float((args or {}).get("y", 80)),
            "w": 240.0,
            "h": 240.0,
            "payload": {"file_id": file_id, "alt": (args or {}).get("alt", ""), "mime": "image/png"},
        },
        author_kind="agent",
        author_id=user_id,
    )
    return {"ok": True, "element_id": el["id"]}
