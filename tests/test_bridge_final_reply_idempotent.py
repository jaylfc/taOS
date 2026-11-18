"""RED tests for tsk-neel3s: retried final reply must not duplicate when
post-commit finalization fails, and a pre-commit failure must not block retry."""
from __future__ import annotations

import json

import pytest

from tinyagentos.bridge_session import BridgeSessionRegistry


class _FakeStore:
    def __init__(self):
        self.messages = {}
        self.states = {}
        self._seq = 0
        self.update_last_message_at_calls = []

    async def send_message(self, **kwargs) -> dict:
        self._seq += 1
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
        if message_id in self.messages:
            self.messages[message_id]["state"] = state

    async def extend_content_blocks(self, message_id: str, blocks: list, cap: int = 50) -> list:
        current = []
        if message_id in self.messages:
            current = self.messages[message_id].get("content_blocks") or []
            if not isinstance(current, list):
                current = []

        # Build a set of existing block identifiers for idempotency
        # Use 'id' field if present, otherwise use the entire block as a JSON string
        def block_key(b: dict) -> str:
            if isinstance(b, dict) and "id" in b:
                return f"id:{b['id']}"
            return f"content:{json.dumps(b, sort_keys=True)}"

        existing_keys = {block_key(b) for b in current}

        # Filter new blocks to only add those not already present
        to_add = []
        for block in blocks:
            key = block_key(block)
            if key not in existing_keys:
                to_add.append(block)
                existing_keys.add(key)

        # Determine how many new blocks we can add without exceeding cap
        available = cap - len(current)
        if available < 0:
            available = 0
        to_add = to_add[:available]

        new_blocks = current + to_add
        if message_id in self.messages:
            self.messages[message_id]["content_blocks"] = new_blocks
        return new_blocks

    async def find_message_by_metadata(
        self, channel_id: str, key: str, value: str, author_id: str | None = None, author_type: str | None = None
    ) -> dict | None:
        for m in self.messages.values():
            if m.get("channel_id") != channel_id:
                continue
            if author_id is not None and m.get("author_id") != author_id:
                continue
            if author_type is not None and m.get("author_type") != author_type:
                continue
            meta = m.get("metadata") or {}
            if meta.get(key) == value:
                return m
        return None

    async def set_metadata(self, message_id: str, metadata: dict) -> None:
        if message_id in self.messages:
            self.messages[message_id]["metadata"] = metadata


class _FakeChannelStore:
    def __init__(self, channels=None, fail_update_after=999):
        self._channels = channels or [{"id": "ch1", "type": "dm", "members": ["bot1"]}]
        self.last_message_at_calls = []
        self._fail_update_after = fail_update_after
        self._update_count = 0

    async def list_channels(self, member_id=None) -> list[dict]:
        if member_id is None:
            return list(self._channels)
        return [c for c in self._channels if member_id in (c.get("members") or [])]

    async def update_last_message_at(self, channel_id: str) -> None:
        self._update_count += 1
        self.last_message_at_calls.append(channel_id)
        if self._update_count > self._fail_update_after:
            raise RuntimeError("update_last_message_at boom")

    async def get_channel(self, channel_id: str) -> dict | None:
        for c in self._channels:
            if c["id"] == channel_id:
                return c
        return None


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


def _make_registry(ch_store=None, msg_store=None, router=None):
    if ch_store is None:
        ch_store = _FakeChannelStore()
    if msg_store is None:
        msg_store = _FakeStore()
    hub = _FakeHub()
    tr = _FakeTraceRegistry()
    reg = BridgeSessionRegistry(
        trace_registry=tr,
        chat_messages=msg_store,
        chat_channels=ch_store,
        chat_hub=hub,
    )
    if router is not None:
        reg._router = router
    return reg, msg_store, ch_store, hub, tr


@pytest.mark.asyncio
async def test_final_reply_post_commit_failure_retry_writes_once_and_finalizes():
    """update_last_message_at raises once: first POST returns 5xx (raises),
    retried POST with same id leaves exactly ONE message row and session finalized."""
    ch_store = _FakeChannelStore(fail_update_after=0)  # fail on first call
    reg, msg_store, ch_store, hub, tr = _make_registry(ch_store=ch_store)

    body = {
        "kind": "final",
        "id": "reply-1",
        "trace_id": "t1",
        "content": "hello",
        "channel_id": "ch1",
    }

    with pytest.raises(RuntimeError, match="update_last_message_at boom"):
        await reg._handle_reply("bot1", body)

    # message was committed before the failure
    assert len(msg_store.messages) == 1
    stored = list(msg_store.messages.values())[0]
    assert stored["content"] == "hello"

    # retry with same reply id - should not create a second row
    ch_store._fail_update_after = 999
    await reg._handle_reply("bot1", body)

    assert len(msg_store.messages) == 1
    stored = list(msg_store.messages.values())[0]
    assert stored["content"] == "hello"
    assert stored.get("state") == "complete" or msg_store.states.get(stored["id"]) == "complete"
    assert ch_store.last_message_at_calls  # finalization ran on retry
    # no duplicate message_out trace for the same logical reply
    out_events = [e for e in tr._store.events if e["kind"] == "message_out"]
    assert len(out_events) == 1


@pytest.mark.asyncio
async def test_final_reply_send_message_raises_before_commit_retry_writes_once():
    """send_message raises before commit: retry writes exactly one row."""
    msg_store = _FakeStore()
    orig = msg_store.send_message
    calls = {"n": 0}

    async def flaky_send(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db write failed before commit")
        return await orig(**kwargs)

    msg_store.send_message = flaky_send  # type: ignore[method-assign]
    reg, msg_store, ch_store, hub, tr = _make_registry(msg_store=msg_store)

    body = {
        "kind": "final",
        "id": "reply-2",
        "trace_id": "t2",
        "content": "hi",
        "channel_id": "ch1",
    }

    with pytest.raises(RuntimeError, match="db write failed before commit"):
        await reg._handle_reply("bot1", body)

    assert len(msg_store.messages) == 0

    await reg._handle_reply("bot1", body)

    assert len(msg_store.messages) == 1
    assert list(msg_store.messages.values())[0]["content"] == "hi"


# --- NEW TESTS FOR THE BUGS ---

@pytest.mark.asyncio
async def test_two_agents_same_channel_same_final_id_create_separate_messages():
    """Two agents in the same channel sending final replies with the same id
    must create TWO separate messages, not edit each other's messages."""
    ch_store = _FakeChannelStore(channels=[{"id": "ch1", "type": "group", "members": ["bot1", "bot2"]}])
    reg, msg_store, ch_store, hub, tr = _make_registry(ch_store=ch_store)

    body1 = {
        "kind": "final",
        "id": "reply-1",
        "trace_id": "t1",
        "content": "hello from bot1",
        "channel_id": "ch1",
    }
    body2 = {
        "kind": "final",
        "id": "reply-1",  # same reply id
        "trace_id": "t2",
        "content": "hello from bot2",
        "channel_id": "ch1",
    }

    await reg.record_reply("bot1", body1)
    await reg.record_reply("bot2", body2)

    # Should have TWO messages, one from each agent
    assert len(msg_store.messages) == 2
    contents = [m["content"] for m in msg_store.messages.values()]
    assert "hello from bot1" in contents
    assert "hello from bot2" in contents
    # Each message should have correct author
    authors = [m["author_id"] for m in msg_store.messages.values()]
    assert authors.count("bot1") == 1
    assert authors.count("bot2") == 1


@pytest.mark.asyncio
async def test_recovery_path_does_not_duplicate_content_blocks():
    """On recovery, extend_content_blocks must not append blocks again if they
    are already present (idempotent by block content or id)."""
    ch_store = _FakeChannelStore(fail_update_after=0)  # fail on first call
    reg, msg_store, ch_store, hub, tr = _make_registry(ch_store=ch_store)

    body = {
        "kind": "final",
        "id": "reply-1",
        "trace_id": "t1",
        "content": "hello",
        "channel_id": "ch1",
        "content_blocks": [{"kind": "text", "text": "hello"}],
    }

    # First call fails during update_last_message_at (swallowed by record_reply)
    await reg.record_reply("bot1", body)

    # message was committed before the failure
    assert len(msg_store.messages) == 1
    stored = list(msg_store.messages.values())[0]
    assert stored["content_blocks"] == [{"kind": "text", "text": "hello"}]

    # retry with same reply id - should not duplicate blocks
    ch_store._fail_update_after = 999
    await reg.record_reply("bot1", body)

    assert len(msg_store.messages) == 1
    stored = list(msg_store.messages.values())[0]
    assert stored["content_blocks"] == [{"kind": "text", "text": "hello"}], f"Got {stored['content_blocks']}"


@pytest.mark.asyncio
async def test_recovery_path_broadcasts_and_dispatches():
    """On recovery, the message_edit and message_state broadcasts must fire,
    and router.dispatch must be called (if router is set)."""
    ch_store = _FakeChannelStore(fail_update_after=0)  # fail on first call
    reg, msg_store, ch_store, hub, tr = _make_registry(ch_store=ch_store)

    body = {
        "kind": "final",
        "id": "reply-1",
        "trace_id": "t1",
        "content": "hello",
        "channel_id": "ch1",
    }

    # First call fails during update_last_message_at (swallowed by record_reply)
    await reg.record_reply("bot1", body)

    # retry with same reply id
    ch_store._fail_update_after = 999
    await reg.record_reply("bot1", body)

    # Should have exactly one message_edit broadcast on recovery
    edits = [b for _, b in hub.broadcasts if b["type"] == "message_edit"]
    assert len(edits) == 1, f"Expected 1 message_edit, got {len(edits)}"
    assert edits[0]["content"] == "hello"
    assert edits[0]["message_id"] == list(msg_store.messages.keys())[0]

    # Should have exactly one message_state broadcast (complete)
    states = [b for _, b in hub.broadcasts if b["type"] == "message_state"]
    assert len(states) == 1, f"Expected 1 message_state, got {len(states)}"
    assert states[0]["state"] == "complete"


@pytest.mark.asyncio
async def test_streaming_placeholder_writes_reply_id_before_first_edit():
    """Failure after first edit but before reply_id written should not cause
    duplicate on retry. The reply_id must be written to the placeholder's
    metadata BEFORE the first edit_message/update_state/extend_content_blocks."""
    msg_store = _FakeStore()
    orig_edit = msg_store.edit_message
    calls = {"n": 0}

    async def flaky_edit(message_id: str, content: str):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("edit failed")
        return await orig_edit(message_id, content)

    msg_store.edit_message = flaky_edit  # type: ignore[method-assign]
    reg, msg_store, ch_store, hub, tr = _make_registry(msg_store=msg_store)

    body = {
        "kind": "delta",
        "trace_id": "t1",
        "content": "Hel",
        "channel_id": "ch1",
    }
    await reg.record_reply("bot1", body)

    body = {
        "kind": "final",
        "id": "reply-1",
        "trace_id": "t1",
        "content": "Hello",
        "channel_id": "ch1",
    }

    # First final fails during edit (swallowed by record_reply)
    await reg.record_reply("bot1", body)

    # Message should exist (placeholder created during delta)
    assert len(msg_store.messages) == 1
    placeholder = list(msg_store.messages.values())[0]
    assert placeholder["content"] == ""
    assert placeholder["state"] == "streaming"
    # The reply_id should be in metadata even though edit failed
    assert placeholder.get("metadata", {}).get("openclaw_msg_id") == "reply-1"

    # Retry - should recover the same message
    calls["n"] = 0
    msg_store.edit_message = orig_edit  # type: ignore[method-assign]
    await reg.record_reply("bot1", body)

    assert len(msg_store.messages) == 1
    stored = list(msg_store.messages.values())[0]
    assert stored["content"] == "Hello"
    assert stored.get("state") == "complete"
    assert stored.get("metadata", {}).get("openclaw_msg_id") == "reply-1"


@pytest.mark.asyncio
async def test_third_retry_no_broadcast_no_change():
    """Third delivery of the same reply id should not broadcast again or change anything."""
    ch_store = _FakeChannelStore(fail_update_after=0)
    reg, msg_store, ch_store, hub, tr = _make_registry(ch_store=ch_store)

    body = {
        "kind": "final",
        "id": "reply-1",
        "trace_id": "t1",
        "content": "hello",
        "channel_id": "ch1",
    }

    # First call fails during update_last_message_at (swallowed by record_reply)
    await reg.record_reply("bot1", body)

    ch_store._fail_update_after = 999
    await reg.record_reply("bot1", body)  # retry 1 - recovers and broadcasts

    broadcast_count_after_retry1 = len(hub.broadcasts)

    await reg.record_reply("bot1", body)  # retry 2 - should be no-op

    broadcast_count_after_retry2 = len(hub.broadcasts)

    assert len(msg_store.messages) == 1
    assert broadcast_count_after_retry2 == broadcast_count_after_retry1, "Third retry should not broadcast"


@pytest.mark.asyncio
async def test_record_reply_drives_code_path_not_handle_reply_directly():
    """Tests must drive through record_reply (which swallows exceptions), not _handle_reply directly.
    The fix covers timeout retries (transport failures), not server errors, because
    routes/openclaw.py calls record_reply which swallows exceptions and always returns 202."""
    reg, msg_store, ch_store, hub, tr = _make_registry()

    body = {
        "kind": "final",
        "id": "reply-1",
        "trace_id": "t1",
        "content": "hello",
        "channel_id": "ch1",
    }

    # This should not raise - record_reply catches exceptions
    await reg.record_reply("bot1", body)
    assert len(msg_store.messages) == 1

    # Broadcast should have fired
    broadcasts = [b for _, b in hub.broadcasts if b["type"] == "message"]
    assert len(broadcasts) == 1