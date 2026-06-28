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
    assert agents == [
        {"agent": "atlas", "standing_instruction": "Research each new idea and reply with feasibility."}
    ]
    # Re-sharing updates the instruction (INSERT OR REPLACE), no duplicate.
    await store.add_member(doc["id"], "agent", "atlas", standing_instruction="Critique it.")
    agents = await store.agent_members(doc["id"])
    assert len(agents) == 1
    assert agents[0]["standing_instruction"] == "Critique it."
    await store.remove_member(doc["id"], "agent", "atlas")
    assert await store.agent_members(doc["id"]) == []


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
