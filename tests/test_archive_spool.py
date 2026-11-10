"""Tests for archive_spool lossless write and drain."""

from __future__ import annotations

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from tinyagentos.archive_spool import record_lossless, drain_spool


class _FakeArchive:
    """Fake archive that can be configured to succeed or fail."""

    def __init__(self, should_fail: bool = False, fail_entries: set[str] | None = None):
        self.should_fail = should_fail
        self.fail_entries = fail_entries or set()
        self.calls = []

    async def record(self, event_type: str, data: dict, *, agent_name=None, summary=None):
        call_key = f"{event_type}:{data.get('content', data.get('tool', ''))}"
        self.calls.append({
            "event_type": event_type,
            "data": data,
            "agent_name": agent_name,
            "summary": summary,
        })
        if self.should_fail and call_key in self.fail_entries:
            raise RuntimeError("archive down")
        if self.should_fail and not self.fail_entries:
            raise RuntimeError("archive down")


@pytest.mark.asyncio
async def test_record_lossless_succeeds_when_archive_works():
    """When archive.record succeeds, no spool file is created."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        archive = _FakeArchive(should_fail=False)

        await record_lossless(
            archive,
            "conversation",
            {"content": "hello", "channel_id": "ch1"},
            agent_name="agent1",
            summary="hello",
            spool_path=spool_path,
        )

        assert len(archive.calls) == 1
        assert archive.calls[0]["event_type"] == "conversation"
        assert archive.calls[0]["data"]["content"] == "hello"
        assert not spool_path.exists()


@pytest.mark.asyncio
async def test_record_lossless_spools_on_archive_failure():
    """When archive.record fails, the event is spooled with exact content."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        archive = _FakeArchive(should_fail=True)

        # Content with leading/trailing whitespace to verify byte-identical preservation
        test_content = "  hello world  \n\t"
        await record_lossless(
            archive,
            "conversation",
            {"content": test_content, "channel_id": "ch1"},
            agent_name="agent1",
            summary="hello",
            spool_path=spool_path,
        )

        assert len(archive.calls) == 1
        assert spool_path.exists()

        lines = spool_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event_type"] == "conversation"
        assert entry["data"]["content"] == test_content  # Exact match, no strip
        assert entry["agent_name"] == "agent1"
        assert entry["summary"] == "hello"
        assert "error" in entry
        assert "archive down" in entry["error"]
        assert entry["utc"]  # Has timestamp


@pytest.mark.asyncio
async def test_record_lossless_caller_does_not_raise():
    """Caller never receives an exception even when archive and spool both fail."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        # Make spool_path a file that can't be written to (e.g., a directory)
        spool_path.mkdir(parents=True, exist_ok=True)

        archive = _FakeArchive(should_fail=True)

        # Should not raise
        await record_lossless(
            archive,
            "conversation",
            {"content": "test"},
            agent_name="agent1",
            summary="test",
            spool_path=spool_path,
        )

        # Call completed without raising


@pytest.mark.asyncio
async def test_drain_spool_replays_and_empties_on_success():
    """drain_spool replays spooled events and removes the spool file on success."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        spool_path.parent.mkdir(parents=True, exist_ok=True)

        # Write two spooled entries
        entry1 = {
            "utc": "2026-01-01T00:00:00+00:00",
            "event_type": "conversation",
            "data": {"content": "msg1"},
            "agent_name": "agent1",
            "summary": "msg1",
            "error": "archive down",
        }
        entry2 = {
            "utc": "2026-01-01T00:00:01+00:00",
            "event_type": "tool_call",
            "data": {"tool": "file_write"},
            "agent_name": "agent2",
            "summary": "file_write",
            "error": "archive down",
        }
        spool_path.write_text(
            json.dumps(entry1, ensure_ascii=False) + "\n" +
            json.dumps(entry2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Now archive works
        archive = _FakeArchive(should_fail=False)

        await drain_spool(archive, spool_path)

        assert len(archive.calls) == 2
        assert archive.calls[0]["event_type"] == "conversation"
        assert archive.calls[0]["data"]["content"] == "msg1"
        assert archive.calls[1]["event_type"] == "tool_call"
        assert archive.calls[1]["data"]["tool"] == "file_write"
        assert not spool_path.exists()


@pytest.mark.asyncio
async def test_drain_spool_keeps_failed_lines():
    """drain_spool keeps lines that still fail and rewrites spool atomically."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        spool_path.parent.mkdir(parents=True, exist_ok=True)

        entry1 = {
            "utc": "2026-01-01T00:00:00+00:00",
            "event_type": "conversation",
            "data": {"content": "msg1"},
            "agent_name": "agent1",
            "summary": "msg1",
            "error": "archive down",
        }
        entry2 = {
            "utc": "2026-01-01T00:00:01+00:00",
            "event_type": "tool_call",
            "data": {"tool": "file_write"},
            "agent_name": "agent2",
            "summary": "file_write",
            "error": "archive down",
        }
        spool_path.write_text(
            json.dumps(entry1, ensure_ascii=False) + "\n" +
            json.dumps(entry2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Archive still fails
        archive = _FakeArchive(should_fail=True)

        await drain_spool(archive, spool_path)

        assert len(archive.calls) == 2  # Both attempted
        assert spool_path.exists()

        lines = spool_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2  # Both kept
        for line in lines:
            entry = json.loads(line)
            assert entry["event_type"] in ("conversation", "tool_call")


@pytest.mark.asyncio
async def test_drain_spool_partial_success():
    """drain_spool removes successful lines, keeps failed ones."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        spool_path.parent.mkdir(parents=True, exist_ok=True)

        entry1 = {
            "utc": "2026-01-01T00:00:00+00:00",
            "event_type": "conversation",
            "data": {"content": "msg1"},
            "agent_name": "agent1",
            "summary": "msg1",
            "error": "archive down",
        }
        entry2 = {
            "utc": "2026-01-01T00:00:01+00:00",
            "event_type": "tool_call",
            "data": {"tool": "file_write"},
            "agent_name": "agent2",
            "summary": "file_write",
            "error": "archive down",
        }
        spool_path.write_text(
            json.dumps(entry1, ensure_ascii=False) + "\n" +
            json.dumps(entry2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Archive fails only for tool_call entries
        archive = _FakeArchive(should_fail=True, fail_entries={"tool_call:file_write"})

        await drain_spool(archive, spool_path)

        assert len(archive.calls) == 2
        assert spool_path.exists()

        lines = spool_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1  # Only the tool_call entry kept
        entry = json.loads(lines[0])
        assert entry["event_type"] == "tool_call"


@pytest.mark.asyncio
async def test_drain_spool_handles_malformed_lines():
    """drain_spool skips malformed JSON lines but keeps them in spool."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        spool_path.parent.mkdir(parents=True, exist_ok=True)

        # Mix of valid and invalid lines
        spool_path.write_text(
            'not valid json\n' +
            json.dumps({
                "utc": "2026-01-01T00:00:00+00:00",
                "event_type": "conversation",
                "data": {"content": "msg1"},
                "agent_name": "agent1",
                "summary": "msg1",
                "error": "archive down",
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        archive = _FakeArchive(should_fail=False)

        await drain_spool(archive, spool_path)

        assert len(archive.calls) == 1
        assert archive.calls[0]["event_type"] == "conversation"

        # Malformed line should be kept
        lines = spool_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        assert lines[0] == 'not valid json'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])