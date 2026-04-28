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


@pytest_asyncio.fixture
async def store_with_member(tmp_path):
    """Provides a canvas store backed by the same DB as a project_members
    table, so permission lookups have something to read."""
    from tinyagentos.projects.project_store import ProjectStore
    db = tmp_path / "shared.db"
    ps = ProjectStore(db)
    await ps.init()
    cs = ProjectCanvasStore(db)
    await cs.init()
    await ps.add_member("p1", "agent-1", member_kind="native")
    yield cs, ps
    await cs.close()
    await ps.close()


@pytest.mark.asyncio
async def test_user_can_always_update(store_with_member):
    cs, _ = store_with_member
    e = await cs.add_element(
        project_id="p1", author_kind="user", author_id="u",
        element={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {"text": "a"}},
    )
    updated = await cs.update_element(
        project_id="p1", element_id=e["id"],
        patch={"x": 50.0, "payload": {"text": "edited"}},
        author_kind="user", author_id="u",
    )
    assert updated["x"] == 50.0
    assert updated["payload"]["text"] == "edited"


@pytest.mark.asyncio
async def test_agent_without_permission_cannot_update(store_with_member):
    from tinyagentos.projects.canvas.store import CanvasPermissionError
    cs, _ = store_with_member
    e = await cs.add_element(
        project_id="p1", author_kind="user", author_id="u",
        element={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {"text": "a"}},
    )
    with pytest.raises(CanvasPermissionError):
        await cs.update_element(
            project_id="p1", element_id=e["id"],
            patch={"x": 50.0},
            author_kind="agent", author_id="agent-1",
        )


@pytest.mark.asyncio
async def test_agent_with_permission_can_update(store_with_member):
    cs, ps = store_with_member
    await cs._db.execute(
        "UPDATE project_members SET can_edit_canvas = 1 WHERE project_id = ? AND member_id = ?",
        ("p1", "agent-1"),
    )
    await cs._db.commit()
    e = await cs.add_element(
        project_id="p1", author_kind="user", author_id="u",
        element={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {"text": "a"}},
    )
    updated = await cs.update_element(
        project_id="p1", element_id=e["id"],
        patch={"x": 50.0},
        author_kind="agent", author_id="agent-1",
    )
    assert updated["x"] == 50.0


@pytest.mark.asyncio
async def test_delete_element_soft_excludes_from_list(store):
    e = await store.add_element(
        project_id="p", author_kind="user", author_id="u",
        element={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {"text": "a"}},
    )
    await store.delete_element(
        project_id="p", element_id=e["id"],
        author_kind="user", author_id="u",
    )
    rows = await store.list_elements("p")
    assert rows == []
    raw = await store.get_element(e["id"])
    assert raw is not None
    assert raw["deleted_at"] is not None


@pytest.mark.asyncio
async def test_delete_requires_permission(store_with_member):
    from tinyagentos.projects.canvas.store import CanvasPermissionError
    cs, _ = store_with_member
    e = await cs.add_element(
        project_id="p1", author_kind="user", author_id="u",
        element={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {"text": "a"}},
    )
    with pytest.raises(CanvasPermissionError):
        await cs.delete_element(
            project_id="p1", element_id=e["id"],
            author_kind="agent", author_id="agent-1",
        )


@pytest_asyncio.fixture
async def store_with_broker(tmp_path):
    from tinyagentos.projects.events import ProjectEventBroker
    broker = ProjectEventBroker()
    s = ProjectCanvasStore(tmp_path / "canvas.db", broker=broker)
    await s.init()
    yield s, broker
    await s.close()


@pytest.mark.asyncio
async def test_add_element_publishes_event(store_with_broker):
    s, broker = store_with_broker
    queue = await broker.subscribe("p1")
    await s.add_element(
        project_id="p1", author_kind="user", author_id="u",
        element={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {"text": "x"}},
    )
    ev = await queue.get()
    assert ev.kind == "canvas.element_added"
    assert ev.payload["element"]["payload"]["text"] == "x"
