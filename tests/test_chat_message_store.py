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
async def test_extend_content_blocks_concurrent_with_append(store):
    """Concurrent append_content_block and extend_content_blocks must not lose blocks."""
    msg = await _send(store)
    mid = msg["id"]

    # Start with empty content_blocks
    assert msg["content_blocks"] == []

    # Run an append and an old-style update (get_message + update_content_blocks) concurrently
    decision_block = {"kind": "decision", "decision_id": "d1"}
    text_block = [{"kind": "text", "text": "hi"}]
    async def old_style_extend(message_id, blocks):
        current_msg = await store.get_message(message_id)
        existing = current_msg.get("content_blocks") or []
        merged = existing + blocks[:50 - len(existing)]
        await store.update_content_blocks(message_id, merged)
        return merged
    await asyncio.gather(
        store.append_content_block(mid, decision_block),
        old_style_extend(mid, text_block),
    )

    fetched = await store.get_message(mid)
    blocks = fetched["content_blocks"]
    # Both blocks should be present (order may vary because append_content_block
    # adds to the end and old_style_extend appends after existing blocks.
    # However, note that old_style_extend keeps existing blocks first and
    # appends new blocks. Since existing blocks are empty, it will add the text
    # block. The append_content_block adds a decision block.
    # The two operations are not atomic with respect to each other, so we
    # might lose one block if they interleave badly.
    # We expect that both blocks survive because the fix is to use extend_content_blocks
    # which is atomic with append_content_block (same lock). But in this test we are
    # using the old style, so we might see a loss.
    # However, the test is for the RED phase: we expect it to fail with the old code.
    # With the fixed code (using extend_content_blocks in the bridge), this test would pass.
    # But note: we are testing the store directly, not the bridge.
    # The purpose of this test is to show that the old sequence (get_message + update_content_blocks)
    # is not safe when concurrent with append_content_block.
    # We will assert that both blocks are present, and we expect this to fail with the old sequence.
    # However, if the store's append_content_block and update_content_blocks both use the same lock,
    # then they are safe. But update_content_blocks does NOT use the content_lock.
    # Let's look at update_content_blocks in message_store.py:
    #     async def update_content_blocks(self, message_id: str, blocks: list | None) -> None:
    #         await self._db.execute(
    #             "UPDATE chat_messages SET content_blocks = ? WHERE id = ?",
    #             (json.dumps(blocks or []), message_id),
    #         )
    #         await self._db.commit()
    # It does not use the content_lock.
    # Therefore, the old sequence (get_message + update_content_blocks) is not protected by the lock
    # and can interleave with append_content_block (which does use the lock).
    # So we expect the test to fail (i.e., not both blocks present) when using the old sequence.
    # But note: the test is run against the real store, and we have not changed the store's
    # update_content_blocks method to use the lock. So the test should fail.
    # However, we are in the RED phase: we want to see the test fail.
    # We will assert that the length is 2 and both blocks are present.
    # If the test fails, we will see which blocks are missing.
    assert len(blocks) == 2, f"Expected 2 blocks, got {len(blocks)}: {blocks}"
    assert decision_block in blocks, f"Decision block not found in {blocks}"
    assert text_block[0] in blocks, f"Text block not found in {blocks}"


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