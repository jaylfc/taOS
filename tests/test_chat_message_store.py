"""Tests for ChatMessageStore's extend_content_blocks and concurrency safety."""
from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

from tinyagentos.chat.message_store import ChatMessageStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ChatMessageStore(tmp_path / "chat.db")
    await s.init()
    yield s
    await s.close()


async def _send(store, channel_id="ch1", author_id="user1", content="hello", **kw):
    return await store.send_message(
        channel_id=channel_id,
        author_id=author_id,
        author_type=kw.pop("author_type", "user"),
        content=content,
        **kw,
    )


@pytest.mark.asyncio
async def test_extend_content_blocks_concurrent_with_append_keeps_both(store):
    """Concurrent append_content_block and extend_content_blocks must not lose blocks."""
    msg = await _send(store)
    mid = msg["id"]

    # Start with empty content_blocks
    assert msg["content_blocks"] == []

    decision_block = {"kind": "decision", "decision_id": "d1"}
    text_block = [{"kind": "text", "text": "hi"}]

    await asyncio.gather(
        store.append_content_block(mid, decision_block),
        store.extend_content_blocks(mid, text_block),
    )

    fetched = await store.get_message(mid)
    blocks = fetched["content_blocks"]
    # Both blocks should be present (order not asserted)
    assert len(blocks) == 2
    assert decision_block in blocks
    assert text_block[0] in blocks


@pytest.mark.asyncio
async def test_extend_content_blocks_respects_cap_and_keeps_existing(store):
    """extend_content_blocks with 49 existing blocks and 3 new -> stored length 50, existing 49 intact."""
    msg = await _send(store)
    mid = msg["id"]

    # Pre-populate with 49 blocks
    existing_blocks = [{"kind": "text", "text": str(i)} for i in range(49)]
    await store.update_content_blocks(mid, existing_blocks)
    fetched = await store.get_message(mid)
    assert len(fetched["content_blocks"]) == 49

    # Try to add 3 blocks, but cap is 50, so only 1 can be added
    new_blocks = [{"kind": "text", "text": f"new{i}"} for i in range(3)]
    result = await store.extend_content_blocks(mid, new_blocks, cap=50)
    assert len(result) == 50  # 49 existing + 1 new

    # Verify the stored blocks
    fetched = await store.get_message(mid)
    stored = fetched["content_blocks"]
    assert len(stored) == 50
    # First 49 should be the original existing blocks
    assert stored[:49] == existing_blocks
    # The 50th should be the first new block (since we can only add one)
    assert stored[49] == new_blocks[0]


@pytest.mark.asyncio
async def test_find_message_by_metadata_with_author_filters(store):
    """find_message_by_metadata must respect author_id and author_type filters."""
    ch1 = "ch1"
    # Create messages from different authors in the same channel
    msg1 = await store.send_message(
        channel_id=ch1,
        author_id="agent1",
        author_type="agent",
        content="from agent1",
        metadata={"openclaw_msg_id": "reply-1"},
    )
    msg2 = await store.send_message(
        channel_id=ch1,
        author_id="agent2",
        author_type="agent",
        content="from agent2",
        metadata={"openclaw_msg_id": "reply-1"},  # same reply_id, different author
    )
    msg3 = await store.send_message(
        channel_id=ch1,
        author_id="user1",
        author_type="user",
        content="from user",
        metadata={"openclaw_msg_id": "reply-1"},  # same reply_id, different author_type
    )

    # Lookup with agent1's author_id should return msg1
    found = await store.find_message_by_metadata(ch1, "openclaw_msg_id", "reply-1", author_id="agent1", author_type="agent")
    assert found is not None
    assert found["id"] == msg1["id"]
    assert found["author_id"] == "agent1"

    # Lookup with agent2's author_id should return msg2
    found = await store.find_message_by_metadata(ch1, "openclaw_msg_id", "reply-1", author_id="agent2", author_type="agent")
    assert found is not None
    assert found["id"] == msg2["id"]
    assert found["author_id"] == "agent2"

    # Lookup with user's author_id should return msg3
    found = await store.find_message_by_metadata(ch1, "openclaw_msg_id", "reply-1", author_id="user1", author_type="user")
    assert found is not None
    assert found["id"] == msg3["id"]
    assert found["author_type"] == "user"

    # Lookup with wrong author_id should return None
    found = await store.find_message_by_metadata(ch1, "openclaw_msg_id", "reply-1", author_id="agent1", author_type="user")
    assert found is None

    # Lookup without author filters should return the first match (order not guaranteed, but should be one of them)
    found = await store.find_message_by_metadata(ch1, "openclaw_msg_id", "reply-1")
    assert found is not None
    assert found["id"] in (msg1["id"], msg2["id"], msg3["id"])

    # Lookup with non-existent key should return None
    found = await store.find_message_by_metadata(ch1, "openclaw_msg_id", "reply-999", author_id="agent1", author_type="agent")
    assert found is None