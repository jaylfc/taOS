"""Tests: archive dual-write for tool_call / tool_result / error / reasoning events in BridgeSessionRegistry."""
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
