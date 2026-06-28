import types

import pytest

from tinyagentos.tools.project_tools import (
    execute_create_project,
    execute_list_projects,
    execute_list_tasks,
    execute_add_task,
    execute_canvas_add_image,
    _slugify,
)


class _FakeProjectStore:
    def __init__(self, owner="user-1"):
        self.calls = []
        self._owner = owner

    async def create_project(self, **kw):
        self.calls.append(kw)
        return {"id": "proj_1", "name": kw["name"]}

    async def get_project(self, project_id):
        if project_id == "missing":
            return None
        return {"id": project_id, "user_id": self._owner, "slug": "luna"}

    async def list_for_user(self, user_id, status="active"):
        self.calls.append({"list_for_user": user_id, "status": status})
        if not user_id:
            return []
        rows = [
            {"id": "proj_1", "name": "Active One", "status": "active"},
            {"id": "proj_2", "name": "Archived One", "status": "archived"},
        ]
        if status is None:
            return rows
        return [r for r in rows if r["status"] == status]


class _FakeTaskStore:
    def __init__(self):
        self.calls = []

    async def create_task(self, **kw):
        self.calls.append(kw)
        return {"id": "task_1", "title": kw["title"]}

    async def list_tasks(self, project_id, status=None):
        rows = [
            {"id": "task_1", "title": "Outline", "status": "open", "project_id": project_id},
            {"id": "task_2", "title": "Draft", "status": "done", "project_id": project_id},
        ]
        if status is not None:
            return [r for r in rows if r["status"] == status]
        return rows


class _FakeCanvasStore:
    def __init__(self):
        self.calls = []

    async def add_element(self, **kw):
        self.calls.append(kw)
        return {"id": "el_1"}


def _req(user_id="user-1", owner="user-1", is_admin=False, base=None):
    state = types.SimpleNamespace(
        project_store=_FakeProjectStore(owner=owner),
        project_task_store=_FakeTaskStore(),
        project_canvas_store=_FakeCanvasStore(),
    )
    if base is not None:
        # config_path.parent is the data dir; projects live under projects_root.
        state.config_path = str(base / "config.json")
        state.projects_root = base / "projects"
    app = types.SimpleNamespace(state=state)
    return types.SimpleNamespace(
        app=app, state=types.SimpleNamespace(user_id=user_id, is_admin=is_admin)
    )


def _seed_generated_image(base, name="img_cover.png"):
    """Create a fake generated image where generate_image would have saved it."""
    d = base / "workspace" / "images" / "generated"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(b"\x89PNG\r\n\x1a\n fake")
    return name


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
async def test_canvas_add_image(tmp_path):
    ref = _seed_generated_image(tmp_path)
    req = _req(base=tmp_path)
    res = await execute_canvas_add_image({"project_id": "proj_1", "image_ref": ref, "alt": "cover"}, req)
    assert res["ok"] and res["element_id"] == "el_1"
    # the generated image was copied into the project's canvas files
    canvas_dir = tmp_path / "projects" / "luna" / "files" / "canvas"
    copied = list(canvas_dir.glob("*.png"))
    assert len(copied) == 1
    call = req.app.state.project_canvas_store.calls[0]
    assert call["author_kind"] == "agent" and call["author_id"] == "user-1"
    el = call["element"]
    assert el["kind"] == "image" and el["payload"]["file_id"] == copied[0].name


@pytest.mark.asyncio
async def test_canvas_add_image_missing_file(tmp_path):
    req = _req(base=tmp_path)
    res = await execute_canvas_add_image({"project_id": "proj_1", "image_ref": "nope.png"}, req)
    assert "error" in res and "not found" in res["error"]


@pytest.mark.asyncio
async def test_add_task_denied_on_other_users_project():
    """Writing to a project the caller does not own is refused."""
    req = _req(user_id="attacker", owner="victim")
    res = await execute_add_task({"project_id": "proj_1", "title": "x"}, req)
    assert res.get("error") == "not your project"
    assert req.app.state.project_task_store.calls == []


@pytest.mark.asyncio
async def test_canvas_add_image_denied_on_other_users_project():
    req = _req(user_id="attacker", owner="victim")
    res = await execute_canvas_add_image({"project_id": "proj_1", "image_ref": "f"}, req)
    assert res.get("error") == "not your project"
    assert req.app.state.project_canvas_store.calls == []


@pytest.mark.asyncio
async def test_admin_may_write_any_project():
    req = _req(user_id="admin", owner="someone", is_admin=True)
    res = await execute_add_task({"project_id": "proj_1", "title": "ok"}, req)
    assert res["ok"]


@pytest.mark.asyncio
async def test_add_task_missing_project():
    res = await execute_add_task({"project_id": "missing", "title": "x"}, _req())
    assert res.get("error") == "project not found"


@pytest.mark.asyncio
async def test_tools_refuse_without_user():
    assert "error" in await execute_create_project({"name": "x"}, _req(user_id=None))
    assert "error" in await execute_add_task({"project_id": "p", "title": "t"}, _req(user_id=None))
    assert "error" in await execute_canvas_add_image({"project_id": "p", "image_ref": "f"}, _req(user_id=None))


@pytest.mark.asyncio
async def test_export_storybook_renders_pdf(tmp_path):
    from PIL import Image
    from tinyagentos.tools.project_tools import execute_export_storybook
    # seed two generated illustrations where generate_image saves them
    gen = tmp_path / "workspace" / "images" / "generated"
    gen.mkdir(parents=True, exist_ok=True)
    for n, c in (("p1.png", (200, 120, 60)), ("p2.png", (60, 140, 200))):
        Image.new("RGB", (300, 200), c).save(gen / n)
    req = _req(base=tmp_path)
    res = await execute_export_storybook(
        {
            "project_id": "proj_1",
            "title": "Brave Little Fox",
            "author": "taOS",
            "pages": [
                {"text": "A small fox lived under an oak.", "image_ref": "p1.png"},
                {"text": "Each morning it explored the forest.", "image_ref": "p2.png"},
            ],
        },
        req,
    )
    assert res["ok"] is True
    assert res["pages"] == 2
    assert res["url"] == "/api/projects/luna/files/exports/luna.pdf"
    pdf = tmp_path / "projects" / "luna" / "files" / "exports" / "luna.pdf"
    assert pdf.is_file() and pdf.read_bytes()[:5] == b"%PDF-"


@pytest.mark.asyncio
async def test_export_storybook_requires_pages(tmp_path):
    from tinyagentos.tools.project_tools import execute_export_storybook
    req = _req(base=tmp_path)
    res = await execute_export_storybook(
        {"project_id": "proj_1", "title": "X", "pages": []}, req
    )
    assert "error" in res and "pages" in res["error"]


@pytest.mark.asyncio
async def test_export_storybook_rejects_other_users_project(tmp_path):
    from tinyagentos.tools.project_tools import execute_export_storybook
    req = _req(user_id="user-2", owner="user-1", base=tmp_path)
    res = await execute_export_storybook(
        {"project_id": "proj_1", "title": "X", "pages": [{"text": "hi"}]}, req
    )
    assert res.get("error") == "not your project"


@pytest.mark.asyncio
async def test_list_projects_active_default():
    req = _req()
    res = await execute_list_projects({}, req)
    assert res["ok"] is True
    assert res["count"] == 1
    assert res["projects"] == [{"project_id": "proj_1", "name": "Active One", "status": "active"}]


@pytest.mark.asyncio
async def test_list_projects_all():
    req = _req()
    res = await execute_list_projects({"status": "all"}, req)
    assert res["count"] == 2
    assert {p["project_id"] for p in res["projects"]} == {"proj_1", "proj_2"}


@pytest.mark.asyncio
async def test_list_projects_rejects_bad_status():
    res = await execute_list_projects({"status": "bogus"}, _req())
    assert "error" in res


@pytest.mark.asyncio
async def test_list_projects_requires_user():
    res = await execute_list_projects({}, _req(user_id=None))
    assert res["error"] == "no authenticated user"


@pytest.mark.asyncio
async def test_list_tasks_returns_board():
    req = _req()
    res = await execute_list_tasks({"project_id": "proj_1"}, req)
    assert res["ok"] is True and res["count"] == 2
    assert res["tasks"][0] == {"task_id": "task_1", "title": "Outline", "status": "open"}


@pytest.mark.asyncio
async def test_list_tasks_status_filter():
    res = await execute_list_tasks({"project_id": "proj_1", "status": "done"}, _req())
    assert res["count"] == 1 and res["tasks"][0]["task_id"] == "task_2"


@pytest.mark.asyncio
async def test_list_tasks_requires_project_id():
    assert "error" in await execute_list_tasks({}, _req())


@pytest.mark.asyncio
async def test_list_tasks_denied_on_other_users_project():
    res = await execute_list_tasks({"project_id": "proj_1"}, _req(user_id="attacker", owner="victim"))
    assert res.get("error") == "not your project"


@pytest.mark.asyncio
async def test_list_tasks_missing_project():
    res = await execute_list_tasks({"project_id": "missing"}, _req())
    assert res.get("error") == "project not found"
