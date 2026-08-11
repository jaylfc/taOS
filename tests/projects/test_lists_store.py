import asyncio
import time

import pytest
import pytest_asyncio

from tinyagentos.projects.lists_store import ProjectListsStore, ProjectListEntriesStore


@pytest_asyncio.fixture
async def lists_store(tmp_path):
    s = ProjectListsStore(tmp_path / "lists.db")
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def entries_store(tmp_path):
    s = ProjectListEntriesStore(tmp_path / "entries.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_create_list_assigns_lst_prefix(lists_store):
    l = await lists_store.create_list(
        project_id="prj-1", title="Shopping", created_by="user-1"
    )
    assert l["id"].startswith("lst-")
    assert l["project_id"] == "prj-1"
    assert l["title"] == "Shopping"
    assert l["status"] == "active"
    assert l["created_by"] == "user-1"
    assert l["created_at"] == l["updated_at"]


@pytest.mark.asyncio
async def test_get_list_returns_none_for_missing(lists_store):
    assert await lists_store.get_list("lst-missing") is None


@pytest.mark.asyncio
async def test_list_lists_scoped_to_project(lists_store):
    await lists_store.create_list(project_id="prj-1", title="A", created_by="u")
    await lists_store.create_list(project_id="prj-2", title="B", created_by="u")
    items = await lists_store.list_lists("prj-1")
    assert len(items) == 1
    assert items[0]["title"] == "A"


@pytest.mark.asyncio
async def test_update_list_changes_fields(lists_store):
    l = await lists_store.create_list(
        project_id="prj-1", title="Original", created_by="u"
    )
    updated = await lists_store.update_list(l["id"], title="Renamed", status="archived")
    assert updated["title"] == "Renamed"
    assert updated["status"] == "archived"
    assert updated["updated_at"] > l["updated_at"]


@pytest.mark.asyncio
async def test_delete_list(lists_store):
    l = await lists_store.create_list(
        project_id="prj-1", title="Gone", created_by="u"
    )
    assert await lists_store.delete_list(l["id"]) is True
    assert await lists_store.get_list(l["id"]) is None


@pytest.mark.asyncio
async def test_delete_nonexistent_list(lists_store):
    assert await lists_store.delete_list("lst-missing") is False


@pytest.mark.asyncio
async def test_add_entry_then_get_entry_round_trip(entries_store):
    e = await entries_store.add_entry(
        list_id="lst-1",
        project_id="prj-1",
        text="Buy milk",
        original_text="Buy milk",
        author_kind="agent",
        author_id="agent-1",
        position=0,
    )
    assert e["id"].startswith("ent-")
    assert e["list_id"] == "lst-1"
    assert e["project_id"] == "prj-1"
    assert e["text"] == "Buy milk"
    assert e["original_text"] == "Buy milk"
    assert e["position"] == 0

    again = await entries_store.get_entry(e["id"])
    assert again["id"] == e["id"]
    assert again["text"] == "Buy milk"
    assert again["original_text"] == "Buy milk"


@pytest.mark.asyncio
async def test_list_entries_ordered_by_position(entries_store):
    await entries_store.add_entry(
        list_id="lst-1", project_id="prj-1", text="B", original_text="B",
        author_kind="agent", author_id="agent-1", position=1,
    )
    await entries_store.add_entry(
        list_id="lst-1", project_id="prj-1", text="A", original_text="A",
        author_kind="agent", author_id="agent-1", position=0,
    )
    await entries_store.add_entry(
        list_id="lst-1", project_id="prj-1", text="C", original_text="C",
        author_kind="agent", author_id="agent-1", position=2,
    )
    items = await entries_store.list_entries(project_id="prj-1", list_id="lst-1")
    assert [item["text"] for item in items] == ["A", "B", "C"]
    assert [item["position"] for item in items] == [0, 1, 2]


@pytest.mark.asyncio
async def test_update_entry_preserves_original_text(entries_store):
    e = await entries_store.add_entry(
        list_id="lst-1",
        project_id="prj-1",
        text="Get groceries",
        original_text="Get groceries",
        author_kind="agent",
        author_id="agent-1",
    )
    updated = await entries_store.update_entry(e["id"], text="Get groceries tidied")
    assert updated is not None
    assert updated["text"] == "Get groceries tidied"
    assert updated["original_text"] == "Get groceries"


@pytest.mark.asyncio
async def test_delete_entry(entries_store):
    e = await entries_store.add_entry(
        list_id="lst-1",
        project_id="prj-1",
        text="Buy milk",
        original_text="Buy milk",
        author_kind="agent",
        author_id="agent-1",
    )
    assert await entries_store.delete_entry(e["id"]) is True
    assert await entries_store.get_entry(e["id"]) is None


@pytest.mark.asyncio
async def test_delete_nonexistent_entry(entries_store):
    assert await entries_store.delete_entry("ent-missing") is False


@pytest.mark.asyncio
async def test_reorder_entries(entries_store):
    a = await entries_store.add_entry(
        list_id="lst-1", project_id="prj-1", text="A", original_text="A",
        author_kind="agent", author_id="agent-1", position=0,
    )
    b = await entries_store.add_entry(
        list_id="lst-1", project_id="prj-1", text="B", original_text="B",
        author_kind="agent", author_id="agent-1", position=1,
    )
    c = await entries_store.add_entry(
        list_id="lst-1", project_id="prj-1", text="C", original_text="C",
        author_kind="agent", author_id="agent-1", position=2,
    )
    await entries_store.reorder_entries(
        project_id="prj-1",
        list_id="lst-1",
        entries=[
            {"id": c["id"], "position": 0},
            {"id": a["id"], "position": 1},
            {"id": b["id"], "position": 2},
        ],
    )
    items = await entries_store.list_entries(project_id="prj-1", list_id="lst-1")
    assert [item["text"] for item in items] == ["C", "A", "B"]


@pytest.mark.asyncio
async def test_get_next_position_returns_sequential_values(entries_store):
    a = await entries_store.add_entry(
        list_id="lst-1", project_id="prj-1", text="A", original_text="A",
        author_kind="agent", author_id="agent-1", position=0,
    )
    assert await entries_store._get_next_position("prj-1", "lst-1") == 1

    b = await entries_store.add_entry(
        list_id="lst-1", project_id="prj-1", text="B", original_text="B",
        author_kind="agent", author_id="agent-1", position=1,
    )
    assert await entries_store._get_next_position("prj-1", "lst-1") == 2

    await entries_store.add_entry(
        list_id="lst-1", project_id="prj-1", text="C", original_text="C",
        author_kind="agent", author_id="agent-1", position=2,
    )
    assert await entries_store._get_next_position("prj-1", "lst-1") == 3


@pytest.mark.asyncio
async def test_list_entries_scoped_to_list_and_status(entries_store):
    await entries_store.add_entry(
        list_id="lst-1", project_id="prj-1", text="A", original_text="A",
        author_kind="agent", author_id="agent-1",
    )
    await entries_store.add_entry(
        list_id="lst-2", project_id="prj-1", text="B", original_text="B",
        author_kind="agent", author_id="agent-1",
    )
    c = await entries_store.add_entry(
        list_id="lst-1", project_id="prj-1", text="C", original_text="C",
        author_kind="agent", author_id="agent-1",
    )
    await entries_store.update_entry(c["id"], status="closed")
    assert len(await entries_store.list_entries(project_id="prj-1", list_id="lst-1")) == 2
    assert len(await entries_store.list_entries(project_id="prj-1", status="closed")) == 1


@pytest.mark.asyncio
async def test_add_entries_without_positions_gets_ascending(entries_store):
    """Adding entries without explicit positions should auto-assign distinct
    ascending positions via _get_next_position (not flat 0s)."""
    e1 = await entries_store.add_entry(
        list_id="lst-1",
        project_id="prj-1",
        text="First",
        original_text="First",
        author_kind="agent",
        author_id="agent-1",
    )
    e2 = await entries_store.add_entry(
        list_id="lst-1",
        project_id="prj-1",
        text="Second",
        original_text="Second",
        author_kind="agent",
        author_id="agent-1",
    )
    e3 = await entries_store.add_entry(
        list_id="lst-1",
        project_id="prj-1",
        text="Third",
        original_text="Third",
        author_kind="agent",
        author_id="agent-1",
    )
    positions = [e["position"] for e in (e1, e2, e3)]
    assert positions == [0, 1, 2], (
        f"Expected [0, 1, 2] but got {positions}"
    )


@pytest.mark.asyncio
async def test_reorder_entries_does_not_corrupt_sibling_list(entries_store):
    """A reorder scoped to one list must not reposition an entry that belongs
    to a different list in the same project, and must signal the mismatch."""
    a = await entries_store.add_entry(
        list_id="lst-A",
        project_id="prj-1",
        text="A",
        original_text="A",
        author_kind="agent",
        author_id="agent-1",
        position=0,
    )
    b = await entries_store.add_entry(
        list_id="lst-B",
        project_id="prj-1",
        text="B",
        original_text="B",
        author_kind="agent",
        author_id="agent-1",
        position=0,
    )

    result = await entries_store.reorder_entries(
        project_id="prj-1",
        list_id="lst-A",
        entries=[
            {"id": a["id"], "position": 5},
            {"id": b["id"], "position": 99},
        ],
    )

    a_after = await entries_store.get_entry(a["id"])
    b_after = await entries_store.get_entry(b["id"])
    assert a_after["position"] == 0, "valid update before the mismatch must be rolled back"
    assert b_after["position"] == 0, "sibling-list entry must not be moved"
    assert result is False, "reorder should signal that id does not belong to list"


@pytest.mark.asyncio
async def test_get_entry_does_not_raise_on_description_access(entries_store):
    """get_entry must read cur.description inside the async with block, not
    after the cursor is closed."""
    e = await entries_store.add_entry(
        list_id="lst-1",
        project_id="prj-1",
        text="Test entry",
        original_text="Test entry",
        author_kind="agent",
        author_id="agent-1",
        position=0,
    )
    result = await entries_store.get_entry(e["id"])
    assert result is not None
    assert result["id"] == e["id"]
    assert result["text"] == "Test entry"


@pytest.mark.asyncio
async def test_reorder_entries_rolls_back_on_exception(entries_store):
    """If an UPDATE raises mid-loop, reorder_entries must rollback all prior
    partial updates so no position change persists, even after a later commit."""
    a = await entries_store.add_entry(
        list_id="lst-1", project_id="prj-1", text="A", original_text="A",
        author_kind="agent", author_id="agent-1", position=0,
    )
    b = await entries_store.add_entry(
        list_id="lst-1", project_id="prj-1", text="B", original_text="B",
        author_kind="agent", author_id="agent-1", position=1,
    )
    original_execute = entries_store._db.execute

    async def fake_execute(sql, params=()):
        if "UPDATE project_list_entries SET position" in sql:
            raise RuntimeError("simulated failure")
        return await original_execute(sql, params)

    entries_store._db.execute = fake_execute

    with pytest.raises(RuntimeError, match="simulated failure"):
        await entries_store.reorder_entries(
            project_id="prj-1",
            list_id="lst-1",
            entries=[
                {"id": a["id"], "position": 10},
                {"id": b["id"], "position": 20},
            ],
        )

    entries_store._db.execute = original_execute
    await entries_store._db.commit()

    a_after = await entries_store.get_entry(a["id"])
    b_after = await entries_store.get_entry(b["id"])
    assert a_after["position"] == 0
    assert b_after["position"] == 1


@pytest.mark.asyncio
async def test_get_next_position_uses_is_null_for_none_list_id(entries_store):
    """_get_next_position with list_id=None must not coerce None to ''.
    It must use the IS NULL branch so that rows with NULL list_id are
    visible (rather than the old list_id = '' path which cannot match NULL)."""
    # With no unfiled rows, should return 0 without error
    result = await entries_store._get_next_position("prj-1", None)
    assert result == 0

    # Seed a row with an explicit list_id; _get_next_position(None) must
    # NOT see it (it belongs to a named list, not the unfiled bucket).
    await entries_store.add_entry(
        list_id="lst-seed",
        project_id="prj-1",
        text="Seeded",
        original_text="Seeded",
        author_kind="agent",
        author_id="agent-1",
        position=0,
    )
    result_after_seed = await entries_store._get_next_position("prj-1", None)
    assert result_after_seed == 0, (
        "_get_next_position(prj-1, None) should not count rows with a "
        f"named list_id, got {result_after_seed}"
    )


@pytest.mark.asyncio
async def test_concurrent_add_entry_distinct_positions(entries_store, monkeypatch):
    """Two sequential add_entry calls without explicit positions must each
    call _get_next_position, not use a hard-coded fallback."""
    positions_seen = []
    original = entries_store._get_next_position

    async def recording_next_position(project_id, list_id):
        val = await original(project_id, list_id)
        positions_seen.append(val)
        return val

    monkeypatch.setattr(entries_store, "_get_next_position", recording_next_position)

    e1 = await entries_store.add_entry(
        list_id="lst-1",
        project_id="prj-1",
        text="First",
        original_text="First",
        author_kind="agent",
        author_id="agent-1",
    )
    e2 = await entries_store.add_entry(
        list_id="lst-1",
        project_id="prj-1",
        text="Second",
        original_text="Second",
        author_kind="agent",
        author_id="agent-1",
    )

    assert e1["position"] == 0
    assert e2["position"] == 1
    assert positions_seen == [0, 1]
