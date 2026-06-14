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
from pathlib import Path
from uuid import uuid4

from fastapi import Request


def _user_id(request: Request) -> str | None:
    return getattr(request.state, "user_id", None) or None


def _data_dir(request: Request) -> Path:
    """Workspace data dir, resolved the same way images.py does."""
    config_path = getattr(request.app.state, "config_path", None)
    if config_path is not None:
        return Path(config_path).parent
    return Path(__file__).parent.parent.parent / "data"


async def _owned_project(request: Request, project_id: str, user_id: str):
    """Return (project, None) if the caller owns project_id (or is admin), else
    (None, error_dict). Prevents writing tasks/images into another user's project."""
    project = await request.app.state.project_store.get_project(project_id)
    if not project:
        return None, {"error": "project not found"}
    is_admin = bool(getattr(request.state, "is_admin", False))
    if not is_admin and project.get("user_id") != user_id:
        return None, {"error": "not your project"}
    return project, None


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
    if not isinstance(project_id, str) or not project_id or not isinstance(title, str) or not title:
        return {"error": "add_task requires 'project_id' and 'title' strings"}
    user_id = _user_id(request)
    if not user_id:
        return {"error": "no authenticated user"}
    _, err = await _owned_project(request, project_id, user_id)
    if err:
        return err
    store = request.app.state.project_task_store
    task = await store.create_task(project_id=project_id, title=title, created_by=user_id)
    return {"ok": True, "task_id": task["id"], "title": task["title"]}


async def execute_canvas_add_image(args: dict, request: Request) -> dict:
    project_id = (args or {}).get("project_id")
    # `image_ref` is the filename returned by generate_image; accept the legacy
    # `file_id` key too for callers that already have a canvas file id.
    image_ref = (args or {}).get("image_ref") or (args or {}).get("file_id")
    if not isinstance(project_id, str) or not project_id or not isinstance(image_ref, str) or not image_ref:
        return {"error": "canvas_add_image requires 'project_id' and 'image_ref' strings"}
    try:
        x = float((args or {}).get("x", 80))
        y = float((args or {}).get("y", 80))
    except (TypeError, ValueError):
        return {"error": "x and y must be numbers"}
    user_id = _user_id(request)
    if not user_id:
        return {"error": "no authenticated user"}
    project, err = await _owned_project(request, project_id, user_id)
    if err:
        return err

    # Copy the generated image (saved by generate_image under the workspace) into
    # the project's canvas files, where the canvas renders it from
    # /api/projects/{slug}/files/canvas/{file_id}. `.name` strips any path part.
    src = _data_dir(request) / "workspace" / "images" / "generated" / Path(image_ref).name
    if not src.is_file():
        return {"error": f"image not found: {image_ref}"}
    slug = project.get("slug") or project_id
    canvas_dir = Path(request.app.state.projects_root) / slug / "files" / "canvas"
    canvas_dir.mkdir(parents=True, exist_ok=True)
    file_id = f"{uuid4().hex}{src.suffix or '.png'}"
    (canvas_dir / file_id).write_bytes(src.read_bytes())

    store = request.app.state.project_canvas_store
    el = await store.add_element(
        project_id=project_id,
        element={
            "kind": "image",
            "x": x,
            "y": y,
            "w": 240.0,
            "h": 240.0,
            "payload": {"file_id": file_id, "alt": (args or {}).get("alt", ""), "mime": "image/png"},
        },
        author_kind="agent",
        author_id=user_id,
    )
    return {"ok": True, "element_id": el["id"], "file_id": file_id}
