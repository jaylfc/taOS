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