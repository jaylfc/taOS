"""Tests for the notes agent tools (notes_list_shared_docs, notes_add_entry)."""
from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest
import pytest_asyncio

from tinyagentos.notes.shared_docs_store import SharedDocsStore
from tinyagentos.tools.notes_tools import (
    execute_notes_add_entry,
    execute_notes_list_shared_docs,
    execute_notes_set_done,
)


# --------------------------------------------------------------------- helpers

def _make_request(store, config=None, msg_store=None):
    state = types.SimpleNamespace(
        shared_docs_store=store,
        config=config,
        chat_messages=msg_store,
    )
    app = types.SimpleNamespace(state=state)
    return types.SimpleNamespace(app=app)


@pytest_asyncio.fixture
async def store(tmp_path):
    s = SharedDocsStore(tmp_path / "test_notes_tools.db")
    await s.init()
    yield s
    await s.close()


# ------------------------------------------------------------------ list tests

@pytest.mark.asyncio
async def test_list_returns_member_docs(store):
    doc = await store.create_doc("user-1", "note", "Shared Ideas")
    await store.add_member(doc["id"], "agent", "atlas")

    req = _make_request(store)
    res = await execute_notes_list_shared_docs({"agent_name": "atlas"}, req)
    assert "docs" in res
    assert any(d["id"] == doc["id"] for d in res["docs"])


@pytest.mark.asyncio
async def test_list_excludes_non_member_docs(store):
    await store.create_doc("user-1", "note", "Private Note")

    req = _make_request(store)
    res = await execute_notes_list_shared_docs({"agent_name": "atlas"}, req)
    assert res["docs"] == []


@pytest.mark.asyncio
async def test_list_excludes_archived_docs(store):
    doc = await store.create_doc("user-1", "note", "Old Note")
    await store.add_member(doc["id"], "agent", "atlas")
    await store.archive_doc(doc["id"])

    req = _make_request(store)
    res = await execute_notes_list_shared_docs({"agent_name": "atlas"}, req)
    assert res["docs"] == []


@pytest.mark.asyncio
async def test_list_missing_agent_name_returns_error(store):
    req = _make_request(store)
    res = await execute_notes_list_shared_docs({}, req)
    assert "error" in res


# ------------------------------------------------------------------ add tests

@pytest.mark.asyncio
async def test_agent_member_can_add_entry(store):
    doc = await store.create_doc("user-1", "list", "Grocery List")
    await store.add_member(doc["id"], "agent", "atlas")

    req = _make_request(store)
    res = await execute_notes_add_entry(
        {"agent_name": "atlas", "doc_id": doc["id"], "text": "Buy milk"},
        req,
    )
    assert res.get("ok") is True
    assert "entry_id" in res

    entries = await store.list_entries(doc["id"])
    assert any(e["text"] == "Buy milk" for e in entries)


@pytest.mark.asyncio
async def test_non_member_agent_rejected(store):
    doc = await store.create_doc("user-1", "note", "Private")

    req = _make_request(store)
    res = await execute_notes_add_entry(
        {"agent_name": "intruder", "doc_id": doc["id"], "text": "Hacked"},
        req,
    )
    assert "error" in res
    assert "permission" in res["error"]

    entries = await store.list_entries(doc["id"])
    assert entries == []


@pytest.mark.asyncio
async def test_add_entry_notifies_other_agents_not_writer(store):
    doc = await store.create_doc("user-1", "note", "Ideas")
    await store.add_member(doc["id"], "agent", "atlas")
    await store.add_member(doc["id"], "agent", "nova")

    sent: list[dict] = []

    async def _fake_send(channel_id, author_id, author_type, content, **kwargs):
        sent.append({"channel_id": channel_id, "content": content})
        return {}

    fake_msg_store = MagicMock()
    fake_msg_store.send_message = _fake_send

    fake_config = types.SimpleNamespace(
        agents=[
            {"name": "atlas", "chat_channel_id": "ch-atlas"},
            {"name": "nova", "chat_channel_id": "ch-nova"},
        ]
    )

    req = _make_request(store, config=fake_config, msg_store=fake_msg_store)
    res = await execute_notes_add_entry(
        {"agent_name": "atlas", "doc_id": doc["id"], "text": "Great idea"},
        req,
    )
    assert res.get("ok") is True

    # nova should be notified; atlas (the writer) should not
    channels_notified = [m["channel_id"] for m in sent]
    assert "ch-nova" in channels_notified
    assert "ch-atlas" not in channels_notified


@pytest.mark.asyncio
async def test_add_entry_missing_fields_returns_error(store):
    req = _make_request(store)

    res = await execute_notes_add_entry({"agent_name": "atlas", "doc_id": "doc-x"}, req)
    assert "error" in res

    res = await execute_notes_add_entry({"agent_name": "atlas", "text": "hi"}, req)
    assert "error" in res

    res = await execute_notes_add_entry({"doc_id": "doc-x", "text": "hi"}, req)
    assert "error" in res


@pytest.mark.asyncio
async def test_agent_cannot_write_archived_doc(store):
    doc = await store.create_doc("user-1", "list", "Old List")
    await store.add_member(doc["id"], "agent", "atlas")
    await store.archive_doc(doc["id"])

    req = _make_request(store)
    res = await execute_notes_add_entry(
        {"agent_name": "atlas", "doc_id": doc["id"], "text": "late entry"},
        req,
    )
    assert "error" in res
    assert "archived" in res["error"]
    assert await store.list_entries(doc["id"]) == []


@pytest.mark.asyncio
async def test_agent_write_attributed_to_agent(store):
    doc = await store.create_doc("user-1", "note", "Ideas")
    await store.add_member(doc["id"], "agent", "atlas")

    req = _make_request(store)
    res = await execute_notes_add_entry(
        {"agent_name": "atlas", "doc_id": doc["id"], "text": "an idea"},
        req,
    )
    revs = await store.list_revisions(res["entry_id"])
    assert revs[0]["editor_type"] == "agent"
    assert revs[0]["editor_id"] == "atlas"


@pytest.mark.asyncio
async def test_list_shared_docs_excludes_internal_fields(store):
    doc = await store.create_doc("user-1", "note", "Shared")
    await store.add_member(doc["id"], "agent", "atlas")

    req = _make_request(store)
    res = await execute_notes_list_shared_docs({"agent_name": "atlas"}, req)
    assert res["docs"]
    keys = set(res["docs"][0].keys())
    assert "owner_user_id" not in keys
    assert keys <= {"id", "kind", "title", "updated_at"}


# -------------------------------------------------------------- set_done tests

@pytest.mark.asyncio
async def test_agent_member_can_mark_task_done(store):
    doc = await store.create_doc("user-1", "list", "Build List")
    await store.add_member(doc["id"], "agent", "atlas")
    entry = await store.add_entry(doc["id"], "Ship the feature", author="user-1")

    req = _make_request(store)
    res = await execute_notes_set_done(
        {"agent_name": "atlas", "doc_id": doc["id"], "entry_id": entry["id"], "done": True},
        req,
    )
    assert res.get("ok") is True
    assert res["done"] is True

    entries = await store.list_entries(doc["id"])
    target = next(e for e in entries if e["id"] == entry["id"])
    assert target["done"] is True

    # And it can be reopened.
    res = await execute_notes_set_done(
        {"agent_name": "atlas", "doc_id": doc["id"], "entry_id": entry["id"], "done": False},
        req,
    )
    assert res.get("ok") is True
    entries = await store.list_entries(doc["id"])
    target = next(e for e in entries if e["id"] == entry["id"])
    assert target["done"] is False


@pytest.mark.asyncio
async def test_viewer_agent_cannot_mark_done(store):
    doc = await store.create_doc("user-1", "list", "Read Only")
    await store.add_member(doc["id"], "agent", "atlas", permission="viewer")
    entry = await store.add_entry(doc["id"], "A task", author="user-1")

    req = _make_request(store)
    res = await execute_notes_set_done(
        {"agent_name": "atlas", "doc_id": doc["id"], "entry_id": entry["id"], "done": True},
        req,
    )
    assert "error" in res
    assert "permission" in res["error"]

    entries = await store.list_entries(doc["id"])
    assert entries[0]["done"] is False


@pytest.mark.asyncio
async def test_non_member_agent_cannot_mark_done(store):
    doc = await store.create_doc("user-1", "list", "Private")
    entry = await store.add_entry(doc["id"], "A task", author="user-1")

    req = _make_request(store)
    res = await execute_notes_set_done(
        {"agent_name": "intruder", "doc_id": doc["id"], "entry_id": entry["id"], "done": True},
        req,
    )
    assert "error" in res
    assert "permission" in res["error"]


@pytest.mark.asyncio
async def test_set_done_rejects_entry_from_another_doc(store):
    doc_a = await store.create_doc("user-1", "list", "List A")
    await store.add_member(doc_a["id"], "agent", "atlas")
    doc_b = await store.create_doc("user-1", "list", "List B")
    foreign = await store.add_entry(doc_b["id"], "Not yours", author="user-1")

    req = _make_request(store)
    res = await execute_notes_set_done(
        {"agent_name": "atlas", "doc_id": doc_a["id"], "entry_id": foreign["id"], "done": True},
        req,
    )
    assert "error" in res
    assert "not found" in res["error"]

    entries = await store.list_entries(doc_b["id"])
    assert entries[0]["done"] is False


@pytest.mark.asyncio
async def test_set_done_on_archived_doc_rejected(store):
    doc = await store.create_doc("user-1", "list", "Old List")
    await store.add_member(doc["id"], "agent", "atlas")
    entry = await store.add_entry(doc["id"], "A task", author="user-1")
    await store.archive_doc(doc["id"])

    req = _make_request(store)
    res = await execute_notes_set_done(
        {"agent_name": "atlas", "doc_id": doc["id"], "entry_id": entry["id"], "done": True},
        req,
    )
    assert "error" in res
    assert "archived" in res["error"]


@pytest.mark.asyncio
async def test_set_done_missing_or_bad_fields_returns_error(store):
    req = _make_request(store)

    # missing done
    res = await execute_notes_set_done({"agent_name": "atlas", "doc_id": "d", "entry_id": "e"}, req)
    assert "error" in res
    # non-boolean done
    res = await execute_notes_set_done(
        {"agent_name": "atlas", "doc_id": "d", "entry_id": "e", "done": "yes"}, req
    )
    assert "error" in res
    # missing entry_id
    res = await execute_notes_set_done({"agent_name": "atlas", "doc_id": "d", "done": True}, req)
    assert "error" in res
