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
        available = cap - len(current)
        if available < 0:
            available = 0
        to_add = blocks[:available]
        new_blocks = current + to_add
        if message_id in self.messages:
            self.messages[message_id]["content_blocks"] = new_blocks
        return new_blocks

    async def find_message_by_metadata(self, channel_id: str, key: str, value: str) -> dict | None:
        for m in self.messages.values():
            if m.get("channel_id") != channel_id:
                continue
            meta = m.get("metadata") or {}
            if meta.get(key) == value:
                return m
        return None


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


def _make_registry(ch_store=None, msg_store=None):
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
