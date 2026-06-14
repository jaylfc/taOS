import types

import pytest

from tinyagentos.tools.project_tools import (
    execute_create_project,
    execute_add_task,
    execute_canvas_add_image,
    _slugify,
)


class _FakeProjectStore:
    def __init__(self):
        self.calls = []

    async def create_project(self, **kw):
        self.calls.append(kw)
        return {"id": "proj_1", "name": kw["name"]}


class _FakeTaskStore:
    def __init__(self):
        self.calls = []

    async def create_task(self, **kw):
        self.calls.append(kw)
        return {"id": "task_1", "title": kw["title"]}


class _FakeCanvasStore:
    def __init__(self):
        self.calls = []

    async def add_element(self, **kw):
        self.calls.append(kw)
        return {"id": "el_1"}


def _req(user_id="user-1"):
    state = types.SimpleNamespace(
        project_store=_FakeProjectStore(),
        project_task_store=_FakeTaskStore(),
        project_canvas_store=_FakeCanvasStore(),
    )
    app = types.SimpleNamespace(state=state)
    return types.SimpleNamespace(app=app, state=types.SimpleNamespace(user_id=user_id))


def test_slugify():
    assert _slugify("Luna and the Lighthouse") == "luna-and-the-lighthouse"
    assert _slugify("  ") == "project"
    assert _slugify("Hello!! World") == "hello-world"


@pytest.mark.asyncio
async def test_create_project():
    req = _req()
    res = await execute_create_project({"name": "Luna and the Lighthouse"}, req)
    assert res["ok"] and res["project_id"] == "proj_1"
    call = req.app.state.project_store.calls[0]
    assert call["name"] == "Luna and the Lighthouse"
    assert call["slug"] == "luna-and-the-lighthouse"
    assert call["created_by"] == "user-1" and call["user_id"] == "user-1"


@pytest.mark.asyncio
async def test_create_project_requires_name():
    assert "error" in await execute_create_project({}, _req())


@pytest.mark.asyncio
async def test_add_task():
    req = _req()
    res = await execute_add_task({"project_id": "proj_1", "title": "Outline the story"}, req)
    assert res["ok"] and res["task_id"] == "task_1"
    call = req.app.state.project_task_store.calls[0]
    assert call["project_id"] == "proj_1" and call["title"] == "Outline the story"


@pytest.mark.asyncio
async def test_add_task_requires_fields():
    assert "error" in await execute_add_task({"project_id": "p"}, _req())


@pytest.mark.asyncio
async def test_canvas_add_image():
    req = _req()
    res = await execute_canvas_add_image({"project_id": "proj_1", "file_id": "img_cover", "alt": "cover"}, req)
    assert res["ok"] and res["element_id"] == "el_1"
    call = req.app.state.project_canvas_store.calls[0]
    assert call["project_id"] == "proj_1"
    assert call["author_kind"] == "agent" and call["author_id"] == "user-1"
    el = call["element"]
    assert el["kind"] == "image" and el["payload"]["file_id"] == "img_cover"


@pytest.mark.asyncio
async def test_tools_refuse_without_user():
    assert "error" in await execute_create_project({"name": "x"}, _req(user_id=None))
    assert "error" in await execute_add_task({"project_id": "p", "title": "t"}, _req(user_id=None))
    assert "error" in await execute_canvas_add_image({"project_id": "p", "file_id": "f"}, _req(user_id=None))
