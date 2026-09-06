"""Tests for Task 12: event payload carries hops/force_respond/context;
replies re-dispatch for agent-to-agent fanout.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from tinyagentos.bridge_session import BridgeSessionRegistry


@pytest.mark.asyncio
async def test_enqueue_passes_through_force_respond_and_context():
    reg = BridgeSessionRegistry()
    await reg.enqueue_user_message("tom", {
        "id": "m1", "trace_id": "m1", "channel_id": "c1",
        "from": "user", "text": "hi", "hops_since_user": 1,
        "force_respond": True, "context": [{"author_id": "user", "author_type": "user", "content": "prev"}],
    })
    frames = []
    async for frame in reg.subscribe("tom"):
        frames.append(frame)
        break
    assert "force_respond" in frames[0]
    assert "hops_since_user" in frames[0]
    assert "prev" in frames[0]  # context serialised into event data


@pytest.mark.asyncio
async def test_handle_reply_sets_hops_on_persisted_reply_metadata():
    store = MagicMock()
    store.get_message = AsyncMock(return_value={"channel_id": "c1"})
    store.send_message = AsyncMock(return_value={
        "id": "r1", "channel_id": "c1", "author_id": "tom",
        "author_type": "agent", "content": "yo", "created_at": 1.0,
        "metadata": {"hops_since_user": 1},
    })
    chans = MagicMock()
    chans.update_last_message_at = AsyncMock()
    hub = MagicMock()
    hub.broadcast = AsyncMock()
    hub.next_seq = MagicMock(return_value=1)
    reg = BridgeSessionRegistry(chat_messages=store, chat_channels=chans, chat_hub=hub)
    # Prime the pending-hops map by simulating enqueue
    await reg.enqueue_user_message("tom", {
        "id": "m1", "trace_id": "m1", "channel_id": "c1", "from": "user",
        "text": "hi", "hops_since_user": 1, "force_respond": False, "context": [],
    })
    # Now post a reply
    await reg.record_reply("tom", {"kind": "final", "id": "r1", "trace_id": "m1", "content": "yo"})
    # send_message should have been called with metadata including hops_since_user=1
    call = store.send_message.await_args
    assert call.kwargs["metadata"]["hops_since_user"] == 1


@pytest.mark.asyncio
async def test_enqueue_passes_thread_id_and_attachments():
    reg = BridgeSessionRegistry()
    await reg.enqueue_user_message("tom", {
        "id": "m1", "trace_id": "m1", "channel_id": "c1",
        "from": "user", "text": "see file", "hops_since_user": 0,
        "force_respond": False, "context": [],
        "thread_id": "t-parent",
        "attachments": [
            {"filename": "a.png", "mime_type": "image/png",
             "size": 1, "url": "/api/chat/files/abc.png"},
        ],
    })
    frames = []
    async for frame in reg.subscribe("tom"):
        frames.append(frame)
        break
    assert "thread_id" in frames[0]
    assert "a.png" in frames[0]
