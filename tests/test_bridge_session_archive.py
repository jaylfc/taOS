"""Tests: archive dual-write for tool_call / tool_result / error / reasoning / final events in BridgeSessionRegistry."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from tinyagentos.bridge_session import BridgeSessionRegistry


# ---------------------------------------------------------------------------
# Minimal fakes (mirrors the pattern in test_bridge_session.py)
# ---------------------------------------------------------------------------

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


def _make_registry(archive=None):
    tr = _FakeTraceRegistry()
    reg = BridgeSessionRegistry(
        trace_registry=tr,
        archive=archive,
    )
    return reg, tr


# ---------------------------------------------------------------------------
# tool_call dual-write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_call_dual_writes_to_archive():
    archive = AsyncMock()
    reg, tr = _make_registry(archive=archive)

    await reg.record_reply("atlas", {
        "kind": "tool_call",
        "trace_id": "t1",
        "tool": "file_write",
        "args": {"path": "hello.txt"},
    })

    # Trace store still called
    assert len(tr._store.events) == 1
    assert tr._store.events[0]["kind"] == "tool_call"

    # Archive now also receives
    archive.record.assert_called_once()
    kwargs = archive.record.call_args.kwargs
    assert kwargs["event_type"] == "tool_call"
    assert kwargs["agent_name"] == "atlas"
    assert kwargs["data"]["tool"] == "file_write"


# ---------------------------------------------------------------------------
# tool_result dual-write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_result_dual_writes_to_archive():
    archive = AsyncMock()
    reg, tr = _make_registry(archive=archive)

    await reg.record_reply("atlas", {
        "kind": "tool_result",
        "trace_id": "t2",
        "tool": "file_write",
        "result": {"ok": True},
        "success": True,
    })

    # Trace store still called
    assert len(tr._store.events) == 1
    assert tr._store.events[0]["kind"] == "tool_result"

    # Archive receives
    archive.record.assert_called_once()
    kwargs = archive.record.call_args.kwargs
    assert kwargs["event_type"] == "tool_result"
    assert kwargs["agent_name"] == "atlas"
    assert kwargs["data"]["tool"] == "file_write"


# ---------------------------------------------------------------------------
# final dual-write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_final_event_dual_writes_to_archive():
    archive = AsyncMock()
    reg, tr = _make_registry(archive=archive)

    await reg.record_reply("atlas", {
        "kind": "final",
        "trace_id": "t9",
        "content": "Final reply text",
    })

    # Trace store still called
    assert len(tr._store.events) == 1
    assert tr._store.events[0]["kind"] == "message_out"

    # Archive receives
    archive.record.assert_called_once()
    kwargs = archive.record.call_args.kwargs
    assert kwargs["event_type"] == "final"
    assert kwargs["agent_name"] == "atlas"
    assert kwargs["data"]["content"] == "Final reply text"
    assert kwargs["data"]["trace_id"] == "t9"
    # reply_id should be None since we didn't provide one
    assert kwargs["data"].get("reply_id") is None


# ---------------------------------------------------------------------------
# Archive failure must not break the trace write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_archive_failure_does_not_break_trace_write():
    archive = AsyncMock()
    archive.record.side_effect = RuntimeError("archive down")
    reg, tr = _make_registry(archive=archive)

    # Must not raise — trace write is the load-bearing path
    await reg.record_reply("atlas", {
        "kind": "tool_call",
        "trace_id": "t3",
        "tool": "t",
        "args": {},
    })

    assert len(tr._store.events) == 1


# ---------------------------------------------------------------------------
# archive=None is tolerated
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_no_archive_is_tolerated():
    reg, tr = _make_registry(archive=None)

    await reg.record_reply("atlas", {
        "kind": "tool_call",
        "trace_id": "t4",
        "tool": "t",
        "args": {},
    })

    assert len(tr._store.events) == 1


# ---------------------------------------------------------------------------
# error dual-write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_error_event_dual_writes_to_archive():
    archive = AsyncMock()
    reg, tr = _make_registry(archive=archive)

    await reg.record_reply("atlas", {
        "kind": "error",
        "trace_id": "t5",
        "error": "something went wrong",
    })

    # Trace store still called
    assert len(tr._store.events) == 1
    assert tr._store.events[0]["kind"] == "error"

    # Archive receives
    archive.record.assert_called_once()
    kwargs = archive.record.call_args.kwargs
    assert kwargs["event_type"] == "error"
    assert kwargs["agent_name"] == "atlas"
    assert kwargs["data"]["error"] == "something went wrong"
    assert kwargs["data"]["trace_id"] == "t5"


# ---------------------------------------------------------------------------
# reasoning dual-write
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reasoning_event_dual_writes_to_archive():
    archive = AsyncMock()
    reg, tr = _make_registry(archive=archive)

    await reg.record_reply("atlas", {
        "kind": "reasoning",
        "trace_id": "t6",
        "content": "I should check the file first",
    })

    # Trace store still called
    assert len(tr._store.events) == 1
    assert tr._store.events[0]["kind"] == "reasoning"

    # Archive receives
    archive.record.assert_called_once()
    kwargs = archive.record.call_args.kwargs
    assert kwargs["event_type"] == "reasoning"
    assert kwargs["agent_name"] == "atlas"
    assert kwargs["data"]["text"] == "I should check the file first"
    assert kwargs["data"]["trace_id"] == "t6"


# ---------------------------------------------------------------------------
# Archive failure must not break trace write for error/reasoning
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_archive_failure_on_error_does_not_break_trace():
    archive = AsyncMock()
    archive.record.side_effect = RuntimeError("archive down")
    reg, tr = _make_registry(archive=archive)

    # Must not raise — trace write is the load-bearing path
    await reg.record_reply("atlas", {
        "kind": "error",
        "trace_id": "t7",
        "error": "boom",
    })

    assert len(tr._store.events) == 1

@pytest.mark.asyncio
async def test_archive_failure_on_reasoning_does_not_break_trace():
    archive = AsyncMock()
    archive.record.side_effect = RuntimeError("archive down")
    reg, tr = _make_registry(archive=archive)

    # Must not raise — trace write is the load-bearing path
    await reg.record_reply("atlas", {
        "kind": "reasoning",
        "trace_id": "t8",
        "content": "thinking...",
    })

    assert len(tr._store.events) == 1


# ---------------------------------------------------------------------------
# final event - test that final reply text appears in exactly one archive record
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_final_event_archive_single_record_verbatim():
    """Test that the bridge reply's final text reaches the taOSmd archive verbatim exactly once."""
    archive = AsyncMock()
    
    # Track all archive.record calls to verify exactly one
    archive_calls = []
    
    async def track_archive_record(event_type, **kwargs):
        archive_calls.append({
            "event_type": event_type,
            "data": kwargs.get("data", {}),
            "agent_name": kwargs.get("agent_name"),
        })
        return None
    
    archive.record.side_effect = track_archive_record
    
    reg, tr = _make_registry(archive=archive)
    
    # Drive a bridge session through to a final event
    # Use channel_id to trigger the chat.py path
    await reg.record_reply("test_agent", {
        "kind": "final",
        "trace_id": "t1",
        "content": "Final reply text",
        "id": "m1",
        "channel_id": "ch1",
    })
    
    # Assert that archive.record was called exactly once
    # Requirement: "the bridge reply's final text reaches the taOSmd archive verbatim exactly once"
    assert len(archive_calls) == 1, f"Expected exactly 1 archive record, got {len(archive_calls)}"
    
    # Assert that the event is of type "final"
    # Requirement: "Do not archive individual `delta` fragments. The final text is their concatenation."
    assert archive_calls[0]["event_type"] == "final", f"Expected event_type='final', got {archive_calls[0]['event_type']}"
    
    # Assert that the content is verbatim (byte-identical)
    assert archive_calls[0]["data"]["content"] == "Final reply text", f"Expected content='Final reply text', got {archive_calls[0]['data']['content']}"
    
    # Assert other fields are present
    assert archive_calls[0]["data"]["trace_id"] == "t1"
    assert archive_calls[0]["data"]["reply_id"] == "m1"
    assert archive_calls[0]["agent_name"] == "test_agent"
    
    # Requirement: "make sure it is not ALSO archived via chat.py (no duplicates)"
    # The archive_calls should only contain the final event from bridge_session.py,
    # not any "conversation" events from chat.py
    event_types = [call["event_type"] for call in archive_calls]
    assert "conversation" not in event_types, f"Final text should not be archived via chat.py (conversation events found: {event_types})"
