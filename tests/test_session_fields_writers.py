"""Tests: every taOS memory writer carries session_id, turn_index and
session_date onto every turn it writes.

Covers bridge_session.py (message_in/message_out/final archive/tool_call),
channel_hub/router.py (inbound + outbound archive), agent_deploy.py
(archive smoke check), save_chunk via chat.py (_capture_user_memory) and
save_chunk via user_workspace.py (_capture_file_activity).

Each writer must include session_id, turn_index and session_date in the data
it persists.
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from tinyagentos.bridge_session import BridgeSessionRegistry
from tinyagentos.channel_hub.message import IncomingMessage, OutgoingMessage
from tinyagentos.channel_hub.router import MessageRouter
from tinyagentos.routes.agent_deploy import archive_smoke_check
from tinyagentos.routes.chat import _session_date_iso

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class _FakeMsgStore:
    def __init__(self):
        self.messages = {}
        self.saved_metadata = []
        self._seq = 0

    async def send_message(self, **kwargs):
        self._seq += 1
        if "content_blocks" in kwargs and kwargs["content_blocks"] is None:
            kwargs["content_blocks"] = []
        msg = {"id": f"msg{self._seq}", **kwargs}
        self.messages[msg["id"]] = msg
        if kwargs.get("metadata"):
            self.saved_metadata.append(kwargs["metadata"])
        return msg

    async def get_message(self, message_id):
        return self.messages.get(message_id)

    async def edit_message(self, message_id, content):
        if message_id in self.messages:
            self.messages[message_id]["content"] = content

    async def update_state(self, message_id, state):
        pass

    async def count_messages(self, channel_id):
        return sum(
            1 for m in self.messages.values() if m.get("channel_id") == channel_id
        )

    async def append_content_block(self, message_id, block):
        return None

    async def extend_content_blocks(self, message_id, blocks, cap=50):
        return []


class _FakeChStore:
    def __init__(self, channels=None):
        self._channels = channels or [{"id": "ch1", "type": "dm", "members": ["bot1"]}]

    async def list_channels(self, member_id=None):
        if member_id is None:
            return list(self._channels)
        return [c for c in self._channels if member_id in (c.get("members") or [])]


class _FakeHub:
    def __init__(self):
        self.broadcasts = []
        self._seq = 0

    def next_seq(self):
        self._seq += 1
        return self._seq

    async def broadcast(self, channel_id, payload):
        self.broadcasts.append((channel_id, payload))


class _FakeTraceStore:
    def __init__(self):
        self.events = []

    async def record(self, kind, **fields):
        self.events.append({"kind": kind, **fields})
        return {"kind": kind, **fields}


class _FakeTraceRegistry:
    def __init__(self):
        self._store = _FakeTraceStore()

    async def get(self, slug):
        return self._store


class _FakeUserMemory:
    def __init__(self):
        self.chunks = []

    async def get_settings(self, user_id):
        return {"capture_conversations": True, "capture_files": True}

    async def save_chunk(self, user_id, content, title, collection, metadata):
        self.chunks.append(
            {"user_id": user_id, "content": content, "title": title,
             "collection": collection, "metadata": metadata}
        )
        return "hash"


class _FakeRequest:
    class app:
        class state:
            archive = None


def _assert_session_fields(data, session_id=None):
    """Every memory write must carry session_id, turn_index, session_date."""
    assert "session_id" in data, f"missing session_id in {data!r}"
    assert "turn_index" in data, f"missing turn_index in {data!r}"
    assert "session_date" in data, f"missing session_date in {data!r}"
    if session_id is not None:
        assert data["session_id"] == session_id, f"{data!r}"


# ---------------------------------------------------------------------------
# bridge_session.py
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bridge_message_in_carry_session_fields():
    reg, msg_store, ch_store, hub, tr = _make_registry()
    await reg.enqueue_user_message("bot1", {
        "id": "u1", "trace_id": "u1", "channel_id": "ch1",
        "from": "user", "text": "hello", "created_at": 1700000000.0,
    })
    evs = [e for e in tr._store.events if e["kind"] == "message_in"]
    assert len(evs) == 1
    data = evs[0]["payload"]
    assert "session_id" in data
    assert data["session_id"] == "ch1"
    assert "turn_index" in data
    assert "session_date" in data


@pytest.mark.asyncio
async def test_bridge_final_archive_carry_session_fields():
    reg, msg_store, ch_store, hub, tr = _make_registry()
    reg._archive = AsyncMock()
    await reg.record_reply("bot1", {
        "kind": "final", "id": "m1", "trace_id": "t1",
        "content": "Hello user", "channel_id": "ch1",
    })
    call = reg._archive.record.call_args
    data = call.kwargs["data"]
    _assert_session_fields(data, "ch1")


@pytest.mark.asyncio
async def test_bridge_message_out_carry_session_fields():
    reg, msg_store, ch_store, hub, tr = _make_registry()
    await reg.record_reply("bot1", {
        "kind": "final", "id": "m1", "trace_id": "t1",
        "content": "Hello user", "channel_id": "ch1",
    })
    evs = [e for e in tr._store.events if e["kind"] == "message_out"]
    assert len(evs) == 1
    data = evs[0]["payload"]
    _assert_session_fields(data, "ch1")


@pytest.mark.asyncio
async def test_bridge_tool_writes_carry_session_fields():
    reg, msg_store, ch_store, hub, tr = _make_registry()
    reg._archive = AsyncMock()
    await reg.record_reply("bot1", {
        "kind": "tool_call", "trace_id": "t1", "channel_id": "ch1",
        "tool": "web_search", "args": {"q": "x"},
    })
    ev = [e for e in tr._store.events if e["kind"] == "tool_call"][0]
    _assert_session_fields(ev["payload"], "ch1")
    data = reg._archive.record.call_args.kwargs["data"]
    _assert_session_fields(data, "ch1")


@pytest.mark.asyncio
async def test_bridge_delta_message_carry_session_fields():
    reg, msg_store, ch_store, hub, tr = _make_registry()
    await reg.record_reply("bot1", {
        "kind": "delta", "trace_id": "t1", "channel_id": "ch1",
        "content": "Hel",
    })
    msgs = [m for m in msg_store.messages.values() if m.get("channel_id") == "ch1"]
    assert len(msgs) == 1
    meta = msgs[0]["metadata"]
    _assert_session_fields(meta, "ch1")


def _make_registry(channels=None):
    ch_store = _FakeChStore(channels)
    msg_store = _FakeMsgStore()
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
# channel_hub/router.py
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_channel_hub_archive_inbound_carry_session_fields():
    router = MessageRouter()
    router._archive = AsyncMock()
    msg = IncomingMessage(
        id="m1", from_id="u1", from_name="Jay",
        platform="discord", channel_id="ch1", channel_name="ch1",
        text="hello",
    )
    await router._archive_message("bot1", msg, "inbound",
                                  platform="discord", channel_id="ch1")
    data = router._archive.record.call_args.args[1]
    _assert_session_fields(data, "ch1")


@pytest.mark.asyncio
async def test_channel_hub_archive_outbound_carry_session_fields():
    router = MessageRouter()
    router._archive = AsyncMock()
    await router._archive_message("bot1", OutgoingMessage(content="hi"),
                                  "outbound", platform="discord", channel_id="ch1")
    data = router._archive.record.call_args.args[1]
    _assert_session_fields(data, "ch1")


# ---------------------------------------------------------------------------
# agent_deploy.py
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_deploy_smoke_check_carry_session_fields():
    archive = AsyncMock()
    req = _FakeRequest()
    req.app.state.archive = archive
    await archive_smoke_check(req, "deploy-slug-1", "none")
    data = archive.record.call_args.kwargs["data"]
    _assert_session_fields(data, "deploy-slug-1")


# ---------------------------------------------------------------------------
# save_chunk via chat.py (_capture_user_memory)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_save_chunk_carry_session_fields():
    from tinyagentos.routes.chat import _capture_user_memory
    um = _FakeUserMemory()
    await _capture_user_memory(
        um,
        content="remember this",
        title="note",
        collection="conversations",
        metadata={"channel_id": "ch1", "message_id": "m1", "timestamp": 1700000000.0},
        setting_key="capture_conversations",
    )
    chunk = um.chunks[-1]
    meta = chunk["metadata"]
    _assert_session_fields(meta, "ch1")
    assert meta["turn_index"] == 0
    assert meta["session_date"] == _session_date_iso(1700000000.0)


# ---------------------------------------------------------------------------
# save_chunk via user_workspace.py (_capture_file_activity)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_user_workspace_save_chunk_carry_session_fields():
    from tinyagentos.routes.user_workspace import _capture_file_activity
    um = _FakeUserMemory()
    await _capture_file_activity(
        um,
        filename="notes.txt",
        path="/workspace/notes.txt",
        size=123,
        action="created",
    )
    chunk = um.chunks[-1]
    meta = chunk["metadata"]
    _assert_session_fields(meta, "/workspace/notes.txt")
    assert meta["turn_index"] == 0
    assert meta["session_date"]
