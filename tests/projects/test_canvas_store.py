import pytest
import pytest_asyncio

from tinyagentos.projects.canvas.store import ProjectCanvasStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ProjectCanvasStore(tmp_path / "canvas.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_store_init_creates_table(store):
    async with store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='project_canvas_elements'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None


@pytest.mark.asyncio
async def test_add_note_persists_and_returns_row(store):
    e = await store.add_element(
        project_id="prj-aaa",
        element={
            "kind": "note",
            "x": 100.0, "y": 200.0, "w": 180.0, "h": 80.0,
            "payload": {"text": "hello", "color": "yellow", "font_size": 14},
        },
        author_kind="user",
        author_id="user-1",
    )
    assert e["id"].startswith("cve-")
    assert e["kind"] == "note"
    assert e["author_kind"] == "user"
    assert e["payload"] == {"text": "hello", "color": "yellow", "font_size": 14}
    assert e["deleted_at"] is None


@pytest.mark.asyncio
async def test_add_element_rejects_unknown_kind(store):
    with pytest.raises(ValueError, match="invalid kind"):
        await store.add_element(
            project_id="p", element={"kind": "doodad", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {}},
            author_kind="user", author_id="u",
        )


@pytest.mark.asyncio
async def test_add_element_rejects_agent_user_shape(store):
    with pytest.raises(ValueError, match="agents may not emit"):
        await store.add_element(
            project_id="p",
            element={"kind": "user_shape", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {}},
            author_kind="agent", author_id="agent-1",
        )


@pytest.mark.asyncio
async def test_list_elements_excludes_other_projects(store):
    a = await store.add_element(
        project_id="p1", author_kind="user", author_id="u",
        element={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {"text": "a"}},
    )
    await store.add_element(
        project_id="p2", author_kind="user", author_id="u",
        element={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {"text": "b"}},
    )
    rows = await store.list_elements("p1")
    assert [r["id"] for r in rows] == [a["id"]]
