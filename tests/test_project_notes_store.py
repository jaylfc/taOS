"""Project notes store: create/read/list/update/delete + newest-first ordering.

The store (tinyagentos/projects/notes_store.py) is exercised directly.  The
routes (tinyagentos/routes/project_notes.py) are exercised over HTTP in
test_routes_project_notes.py to pin the session-or-agent-JWT auth gate.
"""
from __future__ import annotations

import pytest
import pytest_asyncio

from tinyagentos.projects.notes_store import ProjectNotesStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ProjectNotesStore(tmp_path / "projects.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_create_and_get_note(store):
    n = await store.create_note(
        project_id="prj-aaa", title="Idea", body="a thought", author_id="user-1"
    )
    assert n["id"].startswith("note-")
    assert n["project_id"] == "prj-aaa"
    assert n["title"] == "Idea"
    assert n["body"] == "a thought"
    assert n["author_id"] == "user-1"
    assert n["author_kind"] == "user"
    assert n["created_at"] is not None
    assert n["updated_at"] == n["created_at"]

    again = await store.get_note(n["id"])
    assert again == n


@pytest.mark.asyncio
async def test_get_missing_returns_none(store):
    assert await store.get_note("note-does-not-exist") is None


@pytest.mark.asyncio
async def test_list_notes_newest_first(store):
    a = await store.create_note("p", "A", "body-a", "u")
    b = await store.create_note("p", "B", "body-b", "u")
    c = await store.create_note("p", "C", "body-c", "u")
    listed = await store.list_notes("p")
    # Newest first by created_at DESC.
    assert [n["id"] for n in listed] == [c["id"], b["id"], a["id"]]


@pytest.mark.asyncio
async def test_list_notes_scoped_to_project(store):
    n1 = await store.create_note("p1", "one", "b", "u")
    n2 = await store.create_note("p2", "two", "b", "u")
    assert [n["id"] for n in await store.list_notes("p1")] == [n1["id"]]
    assert [n["id"] for n in await store.list_notes("p2")] == [n2["id"]]


@pytest.mark.asyncio
async def test_list_notes_empty(store):
    assert await store.list_notes("p") == []


@pytest.mark.asyncio
async def test_update_note_title_and_body(store):
    n = await store.create_note("p", "orig", "orig body", "u")
    updated = await store.update_note(n["id"], title="new", body="new body")
    assert updated["title"] == "new"
    assert updated["body"] == "new body"
    assert updated["updated_at"] >= n["updated_at"]
    assert updated["author_id"] == "u"


@pytest.mark.asyncio
async def test_update_note_partial_only_sets_provided_fields(store):
    n = await store.create_note("p", "keep me", "keep body", "u")
    updated = await store.update_note(n["id"], title="changed")
    assert updated["title"] == "changed"
    assert updated["body"] == "keep body"


@pytest.mark.asyncio
async def test_update_note_noop_returns_existing(store):
    n = await store.create_note("p", "keep me", "keep body", "u")
    returned = await store.update_note(n["id"])
    assert returned == n


@pytest.mark.asyncio
async def test_update_note_missing_returns_none(store):
    assert await store.update_note("note-missing", title="x") is None


@pytest.mark.asyncio
async def test_delete_note(store):
    n = await store.create_note("p", "doomed", "body", "u")
    assert await store.get_note(n["id"]) is not None
    assert await store.delete_note(n["id"]) is True
    assert await store.get_note(n["id"]) is None


@pytest.mark.asyncio
async def test_delete_note_missing_returns_false(store):
    assert await store.delete_note("note-missing") is False


@pytest.mark.asyncio
async def test_create_note_agent_author_kind(store):
    n = await store.create_note("p", "idea", "body", "agent-7", author_kind="agent")
    assert n["author_kind"] == "agent"
    assert n["author_id"] == "agent-7"


@pytest.mark.asyncio
async def test_migration_creates_index_on_existing_db(tmp_path):
    """A notes table on a fresh install carries the created_at DESC index that
    list_notes relies on for ordering."""
    import sqlite3

    db = tmp_path / "projects.db"
    s = ProjectNotesStore(db)
    await s.init()
    conn = sqlite3.connect(str(db))
    idxs = [r[0] for r in conn.execute(
        "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND tbl_name='project_notes'"
    )]
    conn.close()
    assert "idx_notes_project_created" in idxs
    await s.close()
