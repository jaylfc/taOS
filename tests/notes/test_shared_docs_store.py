"""Tests for the shared notes/lists store."""
import pytest
import pytest_asyncio

from tinyagentos.notes.shared_docs_store import SharedDocsStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = SharedDocsStore(tmp_path / "shared_docs.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_create_note_owner_is_member(store):
    doc = await store.create_doc("user", "note", "App ideas")
    assert doc["kind"] == "note"
    assert doc["title"] == "App ideas"
    assert doc["archived_at"] is None
    members = doc["members"]
    assert any(m["member_type"] == "user" and m["member_id"] == "user" for m in members)


@pytest.mark.asyncio
async def test_invalid_kind_rejected(store):
    with pytest.raises(ValueError):
        await store.create_doc("user", "spreadsheet", "x")


@pytest.mark.asyncio
async def test_entries_round_trip_and_list_done(store):
    doc = await store.create_doc("user", "list", "Groceries")
    e1 = await store.add_entry(doc["id"], "Milk", author="user")
    await store.add_entry(doc["id"], "Eggs", author="user")
    entries = await store.list_entries(doc["id"])
    assert [e["text"] for e in entries] == ["Milk", "Eggs"]
    assert entries[0]["done"] is False
    await store.set_entry_done(e1["id"], True)
    entries = await store.list_entries(doc["id"])
    assert entries[0]["done"] is True


@pytest.mark.asyncio
async def test_share_with_agent_and_standing_instruction(store):
    doc = await store.create_doc("user", "note", "App ideas")
    await store.add_member(
        doc["id"], "agent", "atlas",
        standing_instruction="Research each new idea and reply with feasibility.",
    )
    agents = await store.agent_members(doc["id"])
    assert len(agents) == 1
    assert agents[0]["agent"] == "atlas"
    assert agents[0]["standing_instruction"] == "Research each new idea and reply with feasibility."
    assert agents[0]["permission"] == "contributor"
    assert agents[0]["action"] is None
    # Re-sharing updates the instruction (INSERT OR REPLACE), no duplicate.
    await store.add_member(doc["id"], "agent", "atlas", standing_instruction="Critique it.")
    agents = await store.agent_members(doc["id"])
    assert len(agents) == 1
    assert agents[0]["standing_instruction"] == "Critique it."
    await store.remove_member(doc["id"], "agent", "atlas")
    assert await store.agent_members(doc["id"]) == []


@pytest.mark.asyncio
async def test_add_member_permission_and_action(store):
    doc = await store.create_doc("user", "note", "x")
    await store.add_member(doc["id"], "agent", "nova", permission="editor", action="critique")
    agents = await store.agent_members(doc["id"])
    nova = next(a for a in agents if a["agent"] == "nova")
    assert nova["permission"] == "editor"
    assert nova["action"] == "critique"


@pytest.mark.asyncio
async def test_add_member_invalid_permission_raises(store):
    doc = await store.create_doc("user", "note", "x")
    with pytest.raises(ValueError, match="invalid permission"):
        await store.add_member(doc["id"], "user", "bob", permission="superuser")


@pytest.mark.asyncio
async def test_add_member_invalid_action_raises(store):
    doc = await store.create_doc("user", "note", "x")
    with pytest.raises(ValueError, match="invalid action"):
        await store.add_member(doc["id"], "agent", "atlas", action="sleep")


@pytest.mark.asyncio
async def test_member_permission_helper(store):
    doc = await store.create_doc("alice", "note", "x")
    await store.add_member(doc["id"], "user", "bob", permission="viewer")
    await store.add_member(doc["id"], "user", "carol", permission="editor")

    assert await store.member_permission(doc["id"], "user", "alice", "alice") == "owner"
    assert await store.member_permission(doc["id"], "user", "bob", "alice") == "viewer"
    assert await store.member_permission(doc["id"], "user", "carol", "alice") == "editor"
    assert await store.member_permission(doc["id"], "user", "eve", "alice") is None


@pytest.mark.asyncio
async def test_additive_columns_tolerate_existing_db(tmp_path):
    """A DB created without permission/action columns still works after re-init."""
    import aiosqlite

    db_path = tmp_path / "shared_docs.db"

    # Simulate a pre-existing DB that lacks the new columns.
    async with aiosqlite.connect(str(db_path)) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS shared_docs (
                id TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL,
                kind TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, updated_at REAL NOT NULL, archived_at REAL
            );
            CREATE TABLE IF NOT EXISTS shared_doc_entries (
                id TEXT PRIMARY KEY, doc_id TEXT NOT NULL,
                text TEXT NOT NULL DEFAULT '', done INTEGER NOT NULL DEFAULT 0,
                author TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS shared_doc_members (
                doc_id TEXT NOT NULL, member_type TEXT NOT NULL,
                member_id TEXT NOT NULL,
                standing_instruction TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                PRIMARY KEY (doc_id, member_type, member_id)
            );
            CREATE TABLE IF NOT EXISTS shared_doc_entry_revisions (
                id TEXT PRIMARY KEY, entry_id TEXT NOT NULL,
                rev_index INTEGER NOT NULL, editor_id TEXT NOT NULL DEFAULT '',
                editor_type TEXT NOT NULL DEFAULT 'user', op TEXT NOT NULL,
                diff TEXT, snapshot TEXT, created_at REAL NOT NULL,
                UNIQUE (entry_id, rev_index)
            );
        """)
        await db.commit()

    # Opening with SharedDocsStore must not crash and must add the columns.
    from tinyagentos.notes.shared_docs_store import SharedDocsStore as SDS
    s = SDS(db_path)
    await s.init()
    doc = await s.create_doc("user", "note", "test")
    await s.add_member(doc["id"], "agent", "atlas", permission="editor", action="plan")
    agents = await s.agent_members(doc["id"])
    assert agents[0]["permission"] == "editor"
    assert agents[0]["action"] == "plan"
    await s.close()


@pytest.mark.asyncio
async def test_list_docs_includes_owned_and_shared_excludes_archived(store):
    owned = await store.create_doc("alice", "note", "Mine")
    shared = await store.create_doc("bob", "list", "Bob's")
    await store.add_member(shared["id"], "user", "alice")
    titles = {d["title"] for d in await store.list_docs("alice")}
    assert titles == {"Mine", "Bob's"}
    await store.archive_doc(owned["id"])
    titles = {d["title"] for d in await store.list_docs("alice")}
    assert titles == {"Bob's"}
    assert "Mine" in {d["title"] for d in await store.list_docs("alice", include_archived=True)}


@pytest.mark.asyncio
async def test_add_entry_bumps_updated_at(store):
    doc = await store.create_doc("user", "note", "x")
    before = (await store.get_doc(doc["id"]))["updated_at"]
    await store.add_entry(doc["id"], "new idea")
    after = (await store.get_doc(doc["id"]))["updated_at"]
    assert after >= before
