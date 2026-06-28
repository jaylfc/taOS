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


async def execute_list_projects(args: dict, request: Request) -> dict:
    """List the user's projects so the agent can pick one before adding tasks or
    images, instead of guessing a project_id. Read-only; scoped to the caller."""
    user_id = _user_id(request)
    if not user_id:
        return {"error": "no authenticated user"}
    status = (args or {}).get("status", "active")
    if status not in ("active", "archived", "all"):
        return {"error": "status must be 'active', 'archived', or 'all'"}
    store = request.app.state.project_store
    rows = await store.list_for_user(user_id, status=None if status == "all" else status)
    projects = [
        {"project_id": p.get("id"), "name": p.get("name"), "status": p.get("status")}
        for p in rows
    ]
    return {"ok": True, "projects": projects, "count": len(projects)}


async def execute_list_tasks(args: dict, request: Request) -> dict:
    """List a project's tasks so the agent can review progress or pick the next
    one, instead of guessing. Read-only; scoped to a project the caller owns."""
    project_id = (args or {}).get("project_id")
    if not isinstance(project_id, str) or not project_id:
        return {"error": "list_tasks requires a 'project_id' string"}
    user_id = _user_id(request)
    if not user_id:
        return {"error": "no authenticated user"}
    _, err = await _owned_project(request, project_id, user_id)
    if err:
        return err
    status = (args or {}).get("status")
    if status is not None and not isinstance(status, str):
        return {"error": "status must be a string"}
    store = request.app.state.project_task_store
    rows = await store.list_tasks(project_id, status=status or None)
    tasks = [
        {"task_id": t.get("id"), "title": t.get("title"), "status": t.get("status")}
        for t in rows
    ]
    return {"ok": True, "tasks": tasks, "count": len(tasks)}


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
    # `image_ref` is the filename returned by generate_image (a workspace file).
    image_ref = (args or {}).get("image_ref")
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
    # The slug is the on-disk directory AND the key the canvas render route
    # (/api/projects/{slug}/files/canvas/{file_id}) reads back, so it must stay
    # the project's real slug. New projects slugify safely, but reject a legacy
    # row or fallback id that carries separators rather than escape projects_root.
    slug = project.get("slug") or project_id
    if slug != _slugify(slug):
        return {"error": f"unsafe project slug: {slug!r}"}
    projects_root = Path(request.app.state.projects_root).resolve()
    canvas_dir = (projects_root / slug / "files" / "canvas").resolve()
    if not canvas_dir.is_relative_to(projects_root):
        return {"error": "resolved canvas path escapes projects_root"}
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


async def execute_export_storybook(args: dict, request: Request) -> dict:
    """Render a project's pages + generated illustrations into a storybook PDF
    saved under the project's files/exports, downloadable from the Files app.

    args: project_id, title, pages=[{text, image_ref}], optional cover_image_ref,
    author. image_ref is a filename returned by generate_image (workspace file).
    """
    project_id = (args or {}).get("project_id")
    title = (args or {}).get("title")
    pages_in = (args or {}).get("pages")
    if not isinstance(project_id, str) or not project_id:
        return {"error": "export_storybook requires a 'project_id' string"}
    if not isinstance(title, str) or not title:
        return {"error": "export_storybook requires a 'title' string"}
    if not isinstance(pages_in, list) or not pages_in:
        return {"error": "export_storybook requires a non-empty 'pages' list of {text, image_ref}"}
    user_id = _user_id(request)
    if not user_id:
        return {"error": "no authenticated user"}
    project, err = await _owned_project(request, project_id, user_id)
    if err:
        return err
    slug = project.get("slug") or project_id
    if slug != _slugify(slug):
        return {"error": f"unsafe project slug: {slug!r}"}

    gen_dir = _data_dir(request) / "workspace" / "images" / "generated"

    def _resolve(ref) -> Path | None:
        if not isinstance(ref, str) or not ref:
            return None
        p = gen_dir / Path(ref).name  # .name strips any path part
        return p if p.is_file() else None

    pages = [
        {"text": str(pg.get("text", "")), "image": _resolve(pg.get("image_ref"))}
        for pg in pages_in
        if isinstance(pg, dict)
    ]
    if not pages:
        return {"error": "no valid pages (each needs at least a 'text' string)"}

    projects_root = Path(request.app.state.projects_root).resolve()
    out_dir = (projects_root / slug / "files" / "exports").resolve()
    if not out_dir.is_relative_to(projects_root):
        return {"error": "resolved export path escapes projects_root"}
    out = out_dir / f"{slug}.pdf"

    from tinyagentos.projects.storybook import render_storybook_pdf

    render_storybook_pdf(
        title=title,
        pages=pages,
        out_path=out,
        cover_image=_resolve((args or {}).get("cover_image_ref")),
        author=(args or {}).get("author") or None,
    )
    return {
        "ok": True,
        "file": f"exports/{out.name}",
        "url": f"/api/projects/{slug}/files/exports/{out.name}",
        "pages": len(pages),
    }
