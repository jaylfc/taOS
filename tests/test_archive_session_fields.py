"""Tests: every taOS memory writer carries session_id / turn_index / session_date.

Covers bridge_session.py archive writes, channel_hub/router.py, agent_deploy.py
archive smoke check, and the save_chunk paths in routes/chat.py and
routes/user_workspace.py.  Listed with file:line in the PR body.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tinyagentos.bridge_session import BridgeSessionRegistry
from tinyagentos.channel_hub.message import IncomingMessage, OutgoingMessage
from tinyagentos.channel_hub.router import MessageRouter
from tinyagentos.routes import agent_deploy
from tinyagentos.routes.chat import _capture_user_memory
from tinyagentos.routes.user_workspace import _capture_file_activity


class _FakeChatMessages:
    def __init__(self, count: int) -> None:
        self.count = count

    async def count_messages(self, channel_id: str) -> int:
        return self.count


# ---------------------------------------------------------------------------
# bridge_session.py archive writes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bridge_final_archive_carries_session_fields():
    archive = AsyncMock()
    reg = BridgeSessionRegistry(archive=archive, chat_messages=_FakeChatMessages(2))

    await reg.record_reply("atlas", {
        "kind": "final",
        "trace_id": "t1",
        "content": "Final reply text",
        "channel_id": "ch1",
    })

    kwargs = archive.record.call_args.kwargs
    assert kwargs["event_type"] == "final"
    assert kwargs["data"]["session_id"] == "ch1"
    assert kwargs["data"]["turn_index"] == 2
    assert kwargs["data"]["session_date"] == ""


@pytest.mark.asyncio
async def test_bridge_tool_call_archive_carries_session_fields():
    archive = AsyncMock()
    reg = BridgeSessionRegistry(archive=archive)

    await reg.record_reply("atlas", {
        "kind": "tool_call",
        "trace_id": "t2",
        "tool": "file_write",
        "args": {"path": "hello.txt"},
        "channel_id": "ch2",
    })

    kwargs = archive.record.call_args.kwargs
    assert kwargs["event_type"] == "tool_call"
    assert kwargs["data"]["session_id"] == "ch2"
    assert kwargs["data"]["turn_index"] == 0
    assert kwargs["data"]["session_date"] == ""


@pytest.mark.asyncio
async def test_bridge_tool_result_archive_carries_session_fields():
    archive = AsyncMock()
    reg = BridgeSessionRegistry(archive=archive)

    await reg.record_reply("atlas", {
        "kind": "tool_result",
        "trace_id": "t3",
        "tool": "file_write",
        "result": {"ok": True},
        "success": True,
        "channel_id": "ch3",
    })

    kwargs = archive.record.call_args.kwargs
    assert kwargs["event_type"] == "tool_result"
    assert kwargs["data"]["session_id"] == "ch3"
    assert kwargs["data"]["turn_index"] == 0


@pytest.mark.asyncio
async def test_bridge_error_archive_carries_session_fields():
    archive = AsyncMock()
    reg = BridgeSessionRegistry(archive=archive)

    await reg.record_reply("atlas", {
        "kind": "error",
        "trace_id": "t4",
        "error": "boom",
        "channel_id": "ch4",
    })

    kwargs = archive.record.call_args.kwargs
    assert kwargs["event_type"] == "error"
    assert kwargs["data"]["session_id"] == "ch4"
    assert kwargs["data"]["turn_index"] == 0


@pytest.mark.asyncio
async def test_bridge_reasoning_archive_carries_session_fields():
    archive = AsyncMock()
    reg = BridgeSessionRegistry(archive=archive)

    await reg.record_reply("atlas", {
        "kind": "reasoning",
        "trace_id": "t5",
        "content": "thinking...",
        "channel_id": "ch5",
    })

    kwargs = archive.record.call_args.kwargs
    assert kwargs["event_type"] == "reasoning"
    assert kwargs["data"]["session_id"] == "ch5"
    assert kwargs["data"]["turn_index"] == 0


# ---------------------------------------------------------------------------
# channel_hub/router.py
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_channel_hub_inbound_archive_carries_session_fields():
    archive = AsyncMock()
    router = MessageRouter()
    router.set_archive(archive)

    msg = IncomingMessage(
        id="m1",
        from_id="u1",
        from_name="alice",
        platform="slack",
        channel_id="ch1",
        channel_name="general",
        text="hello",
        timestamp=1_700_000_000,
    )
    await router._archive_message("agent", msg, direction="inbound")

    data = archive.record.call_args.args[1]
    assert data["session_id"] == "ch1"
    assert data["turn_index"] == 0
    assert data["session_date"] != ""


@pytest.mark.asyncio
async def test_channel_hub_outbound_archive_carries_session_fields():
    archive = AsyncMock()
    router = MessageRouter()
    router.set_archive(archive)

    msg = OutgoingMessage(content="hi there")
    await router._archive_message("agent", msg, direction="outbound",
                                  platform="slack", channel_id="ch1")

    data = archive.record.call_args.args[1]
    assert data["session_id"] == "ch1"
    assert data["turn_index"] == 0


# ---------------------------------------------------------------------------
# agent_deploy.py archive smoke check
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_agent_deploy_smoke_archive_carries_session_fields():
    archive = AsyncMock()
    archive.query = AsyncMock(return_value=[{"id": 1}])
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(archive=archive)))

    ok = await agent_deploy.archive_smoke_check(request, "test-agent", "claude-code")
    assert ok is True

    kwargs = archive.record.call_args.kwargs
    assert kwargs["event_type"] == "agent_deployed"
    assert kwargs["data"]["session_id"] == "test-agent"
    assert kwargs["data"]["turn_index"] == 0
    assert kwargs["data"]["session_date"] == ""


# ---------------------------------------------------------------------------
# save_chunk paths (routes/chat.py and routes/user_workspace.py)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_capture_user_memory_save_chunk_carries_session_fields():
    user_memory = AsyncMock()
    user_memory.get_settings = AsyncMock(return_value={"capture_conversations": True})

    await _capture_user_memory(
        user_memory,
        content="hello",
        title="Message in ch1",
        collection="conversations",
        metadata={"channel_id": "ch1", "message_id": "m1", "timestamp": 1_700_000_000},
        setting_key="capture_conversations",
        session_id="ch1",
        turn_index=3,
        session_date="1970-01-01T00:00:00Z",
    )

    kwargs = user_memory.save_chunk.call_args.kwargs
    meta = kwargs["metadata"]
    assert meta["session_id"] == "ch1"
    assert meta["turn_index"] == 3
    assert meta["session_date"] == "1970-01-01T00:00:00Z"


@pytest.mark.asyncio
async def test_user_workspace_save_chunk_carries_session_fields():
    user_memory = AsyncMock()
    user_memory.get_settings = AsyncMock(return_value={"capture_files": True})

    await _capture_file_activity(
        user_memory,
        filename="notes.txt",
        path="notes.txt",
        size=42,
        action="upload",
        session_id="ch1",
        turn_index=1,
        session_date="1970-01-02T00:00:00Z",
    )

    kwargs = user_memory.save_chunk.call_args.kwargs
    meta = kwargs["metadata"]
    assert meta["session_id"] == "ch1"
    assert meta["turn_index"] == 1
    assert meta["session_date"] == "1970-01-02T00:00:00Z"
