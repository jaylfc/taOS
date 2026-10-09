"""Unit tests for BridgeSessionRegistry."""
from __future__ import annotations

import asyncio
import json

import pytest
import pytest_asyncio

from tinyagentos.bridge_session import BridgeSessionRegistry, TICK_INTERVAL


# ---------------------------------------------------------------------------
# Minimal fake stores so tests run without a real DB.
# ---------------------------------------------------------------------------

class _FakeStore:
    def __init__(self):
        self.messages = {}
        self.states = {}
        self.last_message_at_calls = []
        self._seq = 0

    async def send_message(self, **kwargs) -> dict:
        self._seq += 1
        # Ensure content_blocks is a list (default to [] if None)
        if "content_blocks" in kwargs and kwargs["content_blocks"] is None:
            kwargs["content_blocks"] = []
        msg = {"id": f"msg{self._seq}", **kwargs}
        self.messages[msg["id"]] = msg
        return msg

    async def get_message(self, message_id: str) -> dict | None:
        return self.messages.get(message_id)

    async def edit_message(self, message_id: str, content: str) -> None:
        if message_id in self.messages:
            self.messages[message_id]["content"] = content

    async def update_state(self, message_id: str, state: str) -> None:
        self.states[message_id] = state

    async def append_content_block(self, message_id: str, block: dict):
        if message_id not in self.messages:
            return None
        blocks = self.messages[message_id].get("content_blocks") or []
        blocks = list(blocks)
        blocks.append(block)
        self.messages[message_id]["content_blocks"] = blocks
        return blocks

    async def update_content_blocks(self, message_id: str, blocks: list | None) -> None:
        if message_id not in self.messages:
            return
        self.messages[message_id]["content_blocks"] = blocks if blocks is not None else []

    async def extend_content_blocks(self, message_id: str, blocks: list, cap: int = 50) -> list:
        # Simulate the real store: if message_id missing, current is [] (from NULL/'[]' in DB)
        current = []
        if message_id in self.messages:
            current = self.messages[message_id].get("content_blocks") or []
            if not isinstance(current, list):
                current = []
        available = cap - len(current)
        if available < 0:
            available = 0
        to_add = blocks[:available]
        new_blocks = current + to_add
        # Only update if the message exists (real store's UPDATE would affect 0 rows if missing)
        if message_id in self.messages:
            self.messages[message_id]["content_blocks"] = new_blocks
        return new_blocks


class _FakeChannelStore:
    def __init__(self, channels=None):
        self._channels = channels or []
        self.last_message_at_calls = []

    async def list_channels(self, member_id=None) -> list[dict]:
        if member_id is None:
            return list(self._channels)
        return [c for c in self._channels if member_id in (c.get("members") or [])]

    async def update_last_message_at(self, channel_id: str) -> None:
        self.last_message_at_calls.append(channel_id)


class _FakeHub:
    def __init__(self):
        self.broadcasts = []
        self._seq = 0

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def broadcast(self, channel_id: str, payload: dict) -> None:
        self.broadcasts.append((channel_id, payload))


class _FakeTraceStore:
    def __init__(self):
        self.events = []

    async def record(self, kind: str, **fields) -> dict:
        ev = {"kind": kind, **fields}
        self.events.append(ev)
        return ev


class _FakeTraceRegistry:
    def __init__(self):
        self._store = _FakeTraceStore()

    async def get(self, slug: str) -> _FakeTraceStore:
        return self._store


def _make_registry(channels=None):
    ch_store = _FakeChannelStore(channels or [{"id": "ch1", "type": "dm", "members": ["bot1"]}])
    msg_store = _FakeStore()
    hub = _FakeHub()
    tr = _FakeTraceRegistry()
    reg = BridgeSessionRegistry(
        trace_registry=tr,
        chat_messages=msg_store,
        chat_channels=ch_store,
        chat_hub=hub,
    )
    return reg, msg_store, ch_store, hub, tr


# ---------------------------------------------------------------------------
# enqueue + subscribe tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_enqueue_and_receive():
    reg, *_ = _make_registry()
    await reg.enqueue_user_message("bot1", {"id": "u1", "text": "hello"})

    frames = []
    async for frame in reg.subscribe("bot1"):
        frames.append(frame)
        break  # receive just the first frame

    assert len(frames) == 1
    assert "user_message" in frames[0]
    parsed_data = json.loads(frames[0].split("data: ")[1])
    assert parsed_data["id"] == "u1"
    assert parsed_data["text"] == "hello"


@pytest.mark.asyncio
async def test_enqueue_records_message_in_trace():
    """enqueue_user_message must write a message_in trace event under the slug."""
    reg, msg_store, ch_store, hub, tr = _make_registry()

    await reg.enqueue_user_message("bot1", {
        "id": "u42",
        "trace_id": "u42",
        "channel_id": "ch1",
        "from": "user-abc",
        "text": "hello agent",
        "created_at": 123.0,
    })

    in_events = [e for e in tr._store.events if e["kind"] == "message_in"]
    assert len(in_events) == 1
    ev = in_events[0]
    assert ev["trace_id"] == "u42"
    assert ev["channel_id"] == "ch1"
    assert ev["payload"]["from"] == "user-abc"
    assert ev["payload"]["text"] == "hello agent"
    assert ev["payload"]["message_id"] == "u42"
    assert ev["payload"]["author_type"] == "user"


@pytest.mark.asyncio
async def test_enqueue_unknown_slug_no_trace():
    """Enqueueing to an empty or _unknown_ slug must not record a trace event."""
    reg, msg_store, ch_store, hub, tr = _make_registry()

    await reg.enqueue_user_message("", {"id": "x", "text": "nope"})
    await reg.enqueue_user_message("_unknown_", {"id": "y", "text": "also nope"})

    assert [e for e in tr._store.events if e["kind"] == "message_in"] == []


@pytest.mark.asyncio
async def test_enqueue_without_trace_registry_still_queues():
    """If trace_registry is None, enqueue must still push to the SSE queue."""
    reg = BridgeSessionRegistry()  # no deps
    await reg.enqueue_user_message("bot1", {"id": "u1", "text": "hi"})
    # Pull the session queue directly — should contain the user_message event.
    session = reg._sessions["bot1"]
    item = session.queue.get_nowait()
    assert item["event"] == "user_message"
    assert item["data"]["id"] == "u1"


@pytest.mark.asyncio
async def test_subscribe_replaces_old():
    """Second subscriber disconnects the first."""
    reg, *_ = _make_registry()

    received_by_first = []

    async def first_subscriber():
        async for frame in reg.subscribe("bot1"):
            received_by_first.append(frame)
            # stop after one item (if any) or on disconnect

    t1 = asyncio.create_task(first_subscriber())
    await asyncio.sleep(0.01)

    # Second subscriber connects — pushes _DISCONNECT to the first queue.
    async def second_subscriber():
        async for _frame in reg.subscribe("bot1"):
            break

    t2 = asyncio.create_task(second_subscriber())
    await asyncio.sleep(0.01)

    # Enqueue a message — only second subscriber should be live.
    await reg.enqueue_user_message("bot1", {"id": "u2", "text": "second"})
    await asyncio.sleep(0.01)

    t1.cancel()
    t2.cancel()
    await asyncio.gather(t1, t2, return_exceptions=True)
    # First subscriber should have received 0 messages (it was disconnected).
    assert received_by_first == []


@pytest.mark.asyncio
async def test_tick_event():
    """Tick events are labelled correctly."""
    reg, *_ = _make_registry()

    frames = []
    async for frame in reg.subscribe("bot1"):
        frames.append(frame)
        break  # exit immediately via the first tick or message

    # Just ensure any frame has the right SSE shape.
    assert frames[0].startswith("event:")


# ---------------------------------------------------------------------------
# record_reply tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_reply_final_creates_message():
    reg, msg_store, ch_store, hub, tr = _make_registry()
    await reg.record_reply("bot1", {
        "kind": "final",
        "id": "m1",
        "trace_id": "t1",
        "content": "Hello user",
    })
    # A message should have been created in the fake store.
    assert any(m["content"] == "Hello user" for m in msg_store.messages.values())
    # Trace event recorded.
    assert any(e["kind"] == "message_out" for e in tr._store.events)
    # Broadcast fired.
    assert any(p["type"] == "message" for _, p in hub.broadcasts)


@pytest.mark.asyncio
async def test_record_reply_delta_accumulates_then_final():
    reg, msg_store, ch_store, hub, tr = _make_registry()

    await reg.record_reply("bot1", {
        "kind": "delta", "trace_id": "t2", "content": "Hel"
    })
    await reg.record_reply("bot1", {
        "kind": "delta", "trace_id": "t2", "content": "lo"
    })
    await reg.record_reply("bot1", {
        "kind": "final", "trace_id": "t2", "content": ""
    })

    # The final message content should be the accumulated delta.
    completed = [m for m in msg_store.messages.values() if m.get("content")]
    assert any("Hello" in (m.get("content") or "") for m in completed)

    # Check trace event.
    out_events = [e for e in tr._store.events if e["kind"] == "message_out"]
    assert len(out_events) == 1
    assert out_events[0]["payload"]["content"] == "Hello"


@pytest.mark.asyncio
async def test_record_reply_error_sets_state():
    reg, msg_store, ch_store, hub, tr = _make_registry()

    # Create a streaming placeholder first via delta.
    await reg.record_reply("bot1", {
        "kind": "delta", "trace_id": "t3", "content": "..."
    })
    await reg.record_reply("bot1", {
        "kind": "error", "trace_id": "t3", "error": "model crashed"
    })

    # Trace error event recorded.
    error_events = [e for e in tr._store.events if e["kind"] == "error"]
    assert len(error_events) == 1
    assert error_events[0]["payload"]["message"] == "model crashed"


@pytest.mark.asyncio
async def test_record_reply_tool_events():
    reg, msg_store, ch_store, hub, tr = _make_registry()

    await reg.record_reply("bot1", {
        "kind": "tool_call", "trace_id": "t4",
        "tool": "web_search", "args": {"query": "test"}
    })
    await reg.record_reply("bot1", {
        "kind": "tool_result", "trace_id": "t4",
        "tool": "web_search", "result": "results", "success": True
    })

    kinds = [e["kind"] for e in tr._store.events]
    assert "tool_call" in kinds
    assert "tool_result" in kinds


@pytest.mark.asyncio
async def test_record_reply_never_raises():
    """record_reply must catch all exceptions and never propagate."""
    reg = BridgeSessionRegistry()  # no dependencies — everything is None

    # Should not raise even though internals will fail.
    await reg.record_reply("noagent", {"kind": "final", "content": "x", "trace_id": "t"})


@pytest.mark.asyncio
async def test_delta_buffer_flushed_per_trace_id():
    """Different trace_ids have independent buffers."""
    reg, msg_store, *_ = _make_registry()

    await reg.record_reply("bot1", {
        "kind": "delta", "trace_id": "tA", "content": "A"
    })
    await reg.record_reply("bot1", {
        "kind": "delta", "trace_id": "tB", "content": "B"
    })
    await reg.record_reply("bot1", {
        "kind": "final", "trace_id": "tA", "content": ""
    })

    contents = [m["content"] for m in msg_store.messages.values() if m.get("content")]
    assert "A" in contents
    # tB buffer not yet flushed — no message for it yet
    assert "B" not in contents


# ---------------------------------------------------------------------------
# Decision content block attachment (request_decision tool_result -> inline block)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_request_decision_tool_result_attaches_block():
    """A tool_result for request_decision must append a {kind:"decision",
    decision_id} content block to the pending streaming message and broadcast."""
    reg, msg_store, ch_store, hub, tr = _make_registry()

    # Simulate deltas that create a streaming placeholder message.
    await reg.record_reply("bot1", {
        "kind": "delta", "trace_id": "t-dec", "content": "Let me ask...",
    })
    pending_msg_id = reg._sessions["bot1"]._pending_msg_ids["t-dec"]
    assert pending_msg_id is not None

    # tool_result carrying a dict result with decision_id.
    await reg.record_reply("bot1", {
        "kind": "tool_result", "trace_id": "t-dec",
        "tool": "request_decision",
        "result": {"ok": True, "decision_id": "dec-abc", "status": "pending"},
        "success": True,
    })

    msg = msg_store.messages[pending_msg_id]
    blocks = msg.get("content_blocks", [])
    assert {"kind": "decision", "decision_id": "dec-abc"} in blocks

    # A message_edit broadcast carrying the full updated blocks was fired.
    edits = [p for _, p in hub.broadcasts if p["type"] == "message_edit"]
    assert any(p["message_id"] == pending_msg_id for p in edits)
    assert edits[-1]["content_blocks"] == blocks


@pytest.mark.asyncio
async def test_request_decision_json_string_result():
    """The result may arrive as a JSON string; the bridge must parse it."""
    reg, msg_store, ch_store, hub, tr = _make_registry()

    await reg.record_reply("bot1", {
        "kind": "delta", "trace_id": "t-js", "content": "hi",
    })
    pending_msg_id = reg._sessions["bot1"]._pending_msg_ids["t-js"]

    await reg.record_reply("bot1", {
        "kind": "tool_result", "trace_id": "t-js",
        "tool": "request_decision",
        "result": '{"ok": true, "decision_id": "dec-xyz", "status": "pending"}',
        "success": True,
    })

    msg = msg_store.messages[pending_msg_id]
    assert {"kind": "decision", "decision_id": "dec-xyz"} in msg["content_blocks"]


@pytest.mark.asyncio
async def test_non_request_decision_tool_result_no_block():
    """A tool_result for a different tool must NOT attach a decision block."""
    reg, msg_store, ch_store, hub, tr = _make_registry()

    await reg.record_reply("bot1", {
        "kind": "delta", "trace_id": "t-other", "content": "hi",
    })
    pending_msg_id = reg._sessions["bot1"]._pending_msg_ids["t-other"]

    await reg.record_reply("bot1", {
        "kind": "tool_result", "trace_id": "t-other",
        "tool": "web_search",
        "result": {"url": "https://example.com"},
        "success": True,
    })

    msg = msg_store.messages[pending_msg_id]
    assert msg.get("content_blocks") in (None, [])


@pytest.mark.asyncio
async def test_request_decision_no_pending_message_no_block():
    """A decision raised with no chat origin (no pending message) must NOT
    create a chat block -- the block is keyed to an in-flight chat message."""
    reg, msg_store, ch_store, hub, tr = _make_registry()

    # No preceding delta: there is no pending streaming message for this trace.
    await reg.record_reply("bot1", {
        "kind": "tool_result", "trace_id": "t-orphan",
        "tool": "request_decision",
        "result": {"ok": True, "decision_id": "dec-orphan", "status": "pending"},
        "success": True,
    })

    # No message should have been created with a decision block.
    assert not any(
        {"kind": "decision", "decision_id": "dec-orphan"} in (m.get("content_blocks") or [])
        for m in msg_store.messages.values()
    )


@pytest.mark.asyncio
async def test_record_reply_final_with_valid_content_blocks():
    """final with valid content_blocks -> stored message has those blocks and the broadcast includes them."""
    reg, msg_store, ch_store, hub, tr = _make_registry()
    await reg.record_reply("bot1", {
        "kind": "final",
        "id": "m1",
        "trace_id": "t1",
        "content": "Hello user",
        "content_blocks": [
            {"kind": "text", "text": "Hello"},
            {"kind": "thinking", "text": "I am thinking"},
        ],
    })
    # Find the message
    msg = None
    for m in msg_store.messages.values():
        if m.get("content") == "Hello user":
            msg = m
            break
    assert msg is not None
    assert msg["content_blocks"] == [
        {"kind": "text", "text": "Hello"},
        {"kind": "thinking", "text": "I am thinking"},
    ]
    # Check that the broadcast includes the content_blocks
    # There should be a message broadcast (type: message) or message_edit broadcast
    # Since we didn't have a streaming placeholder, it should be a message broadcast.
    broadcasts = [p for _, p in hub.broadcasts if p["type"] == "message"]
    assert len(broadcasts) == 1
    assert broadcasts[0]["content_blocks"] == [
        {"kind": "text", "text": "Hello"},
        {"kind": "thinking", "text": "I am thinking"},
    ]


@pytest.mark.asyncio
async def test_record_reply_final_with_unknown_kind_in_blocks():
    """final with an unknown kind (\"script\") -> message stored with text only, no blocks, no exception."""
    reg, msg_store, ch_store, hub, tr = _make_registry()
    await reg.record_reply("bot1", {
        "kind": "final",
        "id": "m2",
        "trace_id": "t2",
        "content": "Hello user",
        "content_blocks": [
            {"kind": "text", "text": "Hello"},
            {"kind": "script", "text": "alert(1)"},  # invalid kind
        ],
    })
    # Find the message
    msg = None
    for m in msg_store.messages.values():
        if m.get("content") == "Hello user":
            msg = m
            break
    assert msg is not None
    # The content_blocks should be empty list (default) because the invalid blocks were ignored
    assert msg.get("content_blocks") == []
    # No exception should have been raised (we are still here)
    # Check that the broadcast does not include content_blocks (or includes empty list)
    broadcasts = [p for _, p in hub.broadcasts if p["type"] == "message"]
    assert len(broadcasts) == 1
    assert broadcasts[0].get("content_blocks") == []


@pytest.mark.asyncio
async def test_record_reply_final_without_content_blocks():
    """final with no content_blocks -> unchanged behaviour (existing tests stay green)."""
    reg, msg_store, ch_store, hub, tr = _make_registry()
    await reg.record_reply("bot1", {
        "kind": "final",
        "id": "m3",
        "trace_id": "t3",
        "content": "Hello user",
    })
    # Find the message
    msg = None
    for m in msg_store.messages.values():
        if m.get("content") == "Hello user":
            msg = m
            break
    assert msg is not None
    # The content_blocks should be empty list (default)
    assert msg.get("content_blocks") == []
    # The broadcast should not have content_blocks (or empty list)
    broadcasts = [p for _, p in hub.broadcasts if p["type"] == "message"]
    assert len(broadcasts) == 1
    assert broadcasts[0].get("content_blocks") == []
@pytest.mark.asyncio
async def test_record_reply_final_preserves_decision_block_and_merges_content_blocks():
    """delta -> tool_result request_decision -> final with content_blocks should preserve decision block and merge."""
    reg, msg_store, ch_store, hub, tr = _make_registry()
    trace_id = "t1"

    # Step 1: delta creates pending placeholder
    await reg.record_reply("bot1", {
        "kind": "delta",
        "trace_id": trace_id,
        "content": "",
    })
    pending_msg_id = reg._sessions["bot1"]._pending_msg_ids[trace_id]
    assert pending_msg_id is not None

    # Step 2: tool_result for request_decision attaches decision block
    await reg.record_reply("bot1", {
        "kind": "tool_result",
        "trace_id": trace_id,
        "tool": "request_decision",
        "result": {"ok": True, "decision_id": "dec-123"},
        "success": True,
    })

    # Step 3: final with content_blocks
    await reg.record_reply("bot1", {
        "kind": "final",
        "trace_id": trace_id,
        "content": "hi",
        "content_blocks": [{"kind": "text", "text": "hi"}],
    })

    # Retrieve the message
    msg = msg_store.messages.get(pending_msg_id)
    assert msg is not None, "Message should exist"

    blocks = msg.get("content_blocks", [])
    # Expect decision block first, then text block
    assert len(blocks) == 2
    assert blocks[0] == {"kind": "decision", "decision_id": "dec-123"}
    assert blocks[1] == {"kind": "text", "text": "hi"}

    # Check that the message_edit broadcast from the final step has the same blocks
    # Look for the broadcast that has the content we set in the final step.
    edits = [p for _, p in hub.broadcasts if p["type"] == "message_edit" and p["message_id"] == pending_msg_id and p.get("content") == "hi"]
    assert len(edits) == 1, "Expected exactly one message_edit broadcast for this message with content 'hi'"
    assert edits[0]["content_blocks"] == blocks


@pytest.mark.asyncio
async def test_record_reply_final_broadcast_includes_edited_at():
    """final message_edit broadcast must include edited_at field."""
    reg, msg_store, ch_store, hub, tr = _make_registry()
    # Create a pending placeholder to trigger the edit path
    await reg.record_reply("bot1", {
        "kind": "delta",
        "trace_id": "t2",
        "content": "",
    })
    pending_msg_id = reg._sessions["bot1"]._pending_msg_ids["t2"]
    assert pending_msg_id is not None

    # Now send the final
    await reg.record_reply("bot1", {
        "kind": "final",
        "trace_id": "t2",
        "content": "hello",
    })

    # Look for the message_edit broadcast for this message
    edits = [p for _, p in hub.broadcasts if p["type"] == "message_edit" and p["message_id"] == pending_msg_id]
    assert len(edits) == 1
    assert "edited_at" in edits[0]
    # edited_at should be a number (timestamp)
    assert isinstance(edits[0]["edited_at"], (int, float))


@pytest.mark.asyncio
async def test_duplicate_final_reply_is_ingested_once():
    """A retried or duplicated final POST with the same id must not create a
    second chat message or broadcast."""
    reg, msg_store, ch_store, hub, tr = _make_registry()
    body = {
        "kind": "final",
        "id": "m1",
        "trace_id": "t1",
        "content": "hi",
        "channel_id": "c1",
    }
    await reg._handle_reply("bot1", body)
    await reg._handle_reply("bot1", body)
    # send_message should have been called exactly once -> one message stored
    assert len(msg_store.messages) == 1
    # Only one message broadcast
    broadcasts = [p for _, p in hub.broadcasts if p["type"] == "message"]
    assert len(broadcasts) == 1
    # Only one message_out trace event
    out_events = [e for e in tr._store.events if e["kind"] == "message_out"]
    assert len(out_events) == 1


@pytest.mark.asyncio
async def test_distinct_final_ids_are_both_ingested():
    """Two final POSTs with different ids must each create a message."""
    reg, msg_store, ch_store, hub, tr = _make_registry()
    await reg._handle_reply("bot1", {
        "kind": "final",
        "id": "m1",
        "trace_id": "t1",
        "content": "hi",
        "channel_id": "c1",
    })
    await reg._handle_reply("bot1", {
        "kind": "final",
        "id": "m2",
        "trace_id": "t2",
        "content": "hello",
        "channel_id": "c1",
    })
    # Two distinct ids -> two send_message calls -> two messages stored
    assert len(msg_store.messages) == 2
    broadcasts = [p for _, p in hub.broadcasts if p["type"] == "message"]
    assert len(broadcasts) == 2
