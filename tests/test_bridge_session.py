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

    await reg.record_reply("bot1", {"kind": "delta", "trace_id": "tA", "content": "A"})
    await reg.record_reply("bot1", {"kind": "delta", "trace_id": "tB", "content": "B"})
    await reg.record_reply("bot1", {"kind": "final", "trace_id": "tA", "content": ""})

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
