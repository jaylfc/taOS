"""Tests for ChatMessageStore — 19 tests."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pytest
import pytest_asyncio

from tinyagentos.chat.message_store import ChatMessageStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ChatMessageStore(tmp_path / "chat.db")
    await s.init()
    yield s
    await s.close()


# ── helpers ──────────────────────────────────────────────────────────────────

async def _send(store, channel_id="ch1", author_id="user1", content="hello", **kw):
    return await store.send_message(
        channel_id=channel_id,
        author_id=author_id,
        author_type=kw.pop("author_type", "user"),
        content=content,
        **kw,
    )


# ── tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_send_and_get_message(store):
    msg = await _send(store)
    assert msg["id"]
    assert msg["content"] == "hello"
    assert msg["channel_id"] == "ch1"
    assert msg["author_id"] == "user1"
    assert msg["author_type"] == "user"
    assert msg["state"] == "complete"
    assert msg["pinned"] == 0
    assert msg["reactions"] == {}
    assert isinstance(msg["embeds"], list)


@pytest.mark.asyncio
async def test_get_message_returns_none_for_missing(store):
    result = await store.get_message("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_messages_paginate(store):
    for i in range(5):
        await _send(store, content=f"msg{i}")
    msgs = await store.get_messages("ch1", limit=3)
    assert len(msgs) == 3
    # Chronological order
    assert msgs[0]["created_at"] <= msgs[-1]["created_at"]


@pytest.mark.asyncio
async def test_get_messages_before_filter(store):
    m1 = await _send(store, content="first")
    # Small delay to ensure distinct timestamps
    m2 = await _send(store, content="second")
    pivot = m2["created_at"]
    msgs = await store.get_messages("ch1", before=pivot)
    ids = [m["id"] for m in msgs]
    assert m1["id"] in ids
    assert m2["id"] not in ids


@pytest.mark.asyncio
async def test_get_messages_after_filter(store):
    m1 = await _send(store, content="first")
    pivot = m1["created_at"]
    m2 = await _send(store, content="second")
    msgs = await store.get_messages("ch1", after=pivot)
    ids = [m["id"] for m in msgs]
    assert m2["id"] in ids
    assert m1["id"] not in ids


@pytest.mark.asyncio
async def test_send_with_embeds(store):
    embed = {"type": "image", "url": "https://example.com/img.png"}
    msg = await _send(store, embeds=[embed])
    assert msg["embeds"] == [embed]


@pytest.mark.asyncio
async def test_send_with_components(store):
    comp = {"type": "button", "label": "Click me"}
    msg = await _send(store, components=[comp])
    assert msg["components"] == [comp]


@pytest.mark.asyncio
async def test_send_with_attachments(store):
    att = {"id": "file1", "filename": "report.pdf"}
    msg = await _send(store, attachments=[att])
    assert msg["attachments"] == [att]


@pytest.mark.asyncio
async def test_edit_message(store):
    msg = await _send(store, content="original")
    await store.edit_message(msg["id"], "updated")
    updated = await store.get_message(msg["id"])
    assert updated["content"] == "updated"
    assert updated["edited_at"] is not None


@pytest.mark.asyncio
async def test_delete_message(store):
    msg = await _send(store)
    deleted = await store.delete_message(msg["id"])
    assert deleted is True
    got = await store.get_message(msg["id"])
    assert got is not None
    assert got["deleted_at"] is not None


@pytest.mark.asyncio
async def test_add_reaction(store):
    msg = await _send(store)
    await store.add_reaction(msg["id"], "👍", "user2")
    updated = await store.get_message(msg["id"])
    assert "user2" in updated["reactions"]["👍"]


@pytest.mark.asyncio
async def test_remove_reaction(store):
    msg = await _send(store)
    await store.add_reaction(msg["id"], "👍", "user2")
    await store.remove_reaction(msg["id"], "👍", "user2")
    updated = await store.get_message(msg["id"])
    assert "👍" not in updated["reactions"]


@pytest.mark.asyncio
async def test_multiple_reactions(store):
    msg = await _send(store)
    await store.add_reaction(msg["id"], "👍", "user1")
    await store.add_reaction(msg["id"], "👍", "user2")
    await store.add_reaction(msg["id"], "❤️", "user3")
    updated = await store.get_message(msg["id"])
    assert len(updated["reactions"]["👍"]) == 2
    assert "user3" in updated["reactions"]["❤️"]


@pytest.mark.asyncio
async def test_update_state(store):
    msg = await _send(store, state="pending")
    await store.update_state(msg["id"], "streaming")
    updated = await store.get_message(msg["id"])
    assert updated["state"] == "streaming"


@pytest.mark.asyncio
async def test_pin_message(store):
    msg = await _send(store)
    await store.pin_message("ch1", msg["id"], pinned_by="admin")
    pins = await store.get_pins("ch1")
    assert len(pins) == 1
    assert pins[0]["id"] == msg["id"]


@pytest.mark.asyncio
async def test_unpin_message(store):
    msg = await _send(store)
    await store.pin_message("ch1", msg["id"], pinned_by="admin")
    await store.unpin_message("ch1", msg["id"])
    pins = await store.get_pins("ch1")
    assert pins == []


@pytest.mark.asyncio
async def test_search(store):
    await _send(store, content="hello world")
    await _send(store, content="goodbye world")
    await _send(store, content="unrelated")
    results = await store.search("world")
    assert len(results) == 2


@pytest.mark.asyncio
async def test_search_in_channel(store):
    await _send(store, channel_id="ch1", content="alpha in ch1")
    await _send(store, channel_id="ch2", content="alpha in ch2")
    results = await store.search("alpha", channel_id="ch1")
    assert len(results) == 1
    assert results[0]["channel_id"] == "ch1"


@pytest.mark.asyncio
async def test_canvas_message(store):
    """Messages with content_type='canvas' store canvas data as content."""
    canvas_data = '{"type":"canvas","blocks":[{"type":"paragraph","text":"Hello"}]}'
    msg = await _send(store, content=canvas_data, content_type="canvas")
    fetched = await store.get_message(msg["id"])
    assert fetched["content_type"] == "canvas"
    assert fetched["content"] == canvas_data


@pytest.mark.asyncio
async def test_content_blocks(store):
    blocks = [
        {"type": "paragraph", "text": "First block"},
        {"type": "code", "lang": "python", "text": "print('hi')"},
    ]
    msg = await _send(store, content_blocks=blocks)
    fetched = await store.get_message(msg["id"])
    assert fetched["content_blocks"] == blocks


@pytest.mark.asyncio
async def test_send_message_persists_hops_metadata(tmp_path):
    store = ChatMessageStore(tmp_path / "msgs.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent",
        content="yo", content_type="text", state="complete",
        metadata={"hops_since_user": 2, "other": "x"},
    )
    assert msg["metadata"]["hops_since_user"] == 2
    assert msg["metadata"]["other"] == "x"


@pytest.mark.asyncio
async def test_send_message_defaults_hops_zero_when_absent(tmp_path):
    store = ChatMessageStore(tmp_path / "msgs.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="user", author_type="user",
        content="hi", content_type="text", state="complete",
        metadata=None,
    )
    assert msg["metadata"].get("hops_since_user", 0) == 0


@pytest.mark.asyncio
async def test_soft_delete_sets_deleted_at(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await store.send_message(
        channel_id="c1", author_id="tom", author_type="agent",
        content="hello",
    )
    ok = await store.soft_delete_message(msg["id"])
    assert ok is True
    got = await store.get_message(msg["id"])
    assert got is not None  # row preserved
    assert got["deleted_at"] is not None
    assert got["content"] == "hello"  # content preserved for admin recovery


@pytest.mark.asyncio
async def test_soft_delete_nonexistent_returns_false(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    ok = await store.soft_delete_message("does-not-exist")
    assert ok is False


@pytest.mark.asyncio
async def test_append_content_block(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await _send(store)
    blocks = await store.append_content_block(msg["id"], {"kind": "decision", "decision_id": "dec-1"})
    assert blocks is not None
    assert blocks[-1] == {"kind": "decision", "decision_id": "dec-1"}
    fetched = await store.get_message(msg["id"])
    assert fetched["content_blocks"] == blocks


@pytest.mark.asyncio
async def test_concurrent_appends_do_not_drop_blocks(tmp_path):
    """Every concurrent append must survive.

    A read-modify-write in Python interleaves at its await points: two callers
    SELECT the same array, each appends to its own copy, and the second UPDATE
    overwrites the first. The loser disappears with no error and no log line,
    so this asserts on the surviving SET rather than on any ordering.
    """
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await _send(store)
    count = 12
    await asyncio.gather(
        *(
            store.append_content_block(msg["id"], {"kind": "text", "text": f"b{i}"})
            for i in range(count)
        )
    )
    fetched = await store.get_message(msg["id"])
    assert len(fetched["content_blocks"]) == count
    assert {b["text"] for b in fetched["content_blocks"]} == {
        f"b{i}" for i in range(count)
    }


@pytest.mark.asyncio
async def test_append_content_block_missing_message(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    result = await store.append_content_block("nope", {"kind": "decision", "decision_id": "dec-1"})
    assert result is None


@pytest.mark.asyncio
async def test_append_multiple_content_blocks(tmp_path):
    store = ChatMessageStore(tmp_path / "chat.db")
    await store.init()
    msg = await _send(store)
    await store.append_content_block(msg["id"], {"kind": "decision", "decision_id": "dec-1"})
    await store.append_content_block(msg["id"], {"kind": "text", "text": "follow-up"})
    fetched = await store.get_message(msg["id"])
    assert fetched["content_blocks"] == [
        {"kind": "decision", "decision_id": "dec-1"},
        {"kind": "text", "text": "follow-up"},
    ]
