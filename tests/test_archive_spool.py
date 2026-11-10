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


@pytest.mark.asyncio
async def test_append_during_drain_survives():
    """Events appended during drain_spool are not deleted."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        spool_path.parent.mkdir(parents=True, exist_ok=True)

        # Write one spooled entry
        entry1 = {
            "utc": "2026-01-01T00:00:00+00:00",
            "event_type": "conversation",
            "data": {"content": "msg1"},
            "agent_name": "agent1",
            "summary": "msg1",
            "error": "archive down",
        }
        spool_path.write_text(
            json.dumps(entry1, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Fake archive that blocks on first record call
        drain_event = asyncio.Event()

        class BlockingArchive:
            def __init__(self):
                self.calls = []

            async def record(self, event_type: str, data: dict, *, agent_name=None, summary=None):
                self.calls.append({
                    "event_type": event_type,
                    "data": data,
                    "agent_name": agent_name,
                    "summary": summary,
                })
                if event_type == "conversation" and data.get("content") == "msg1":
                    await drain_event.wait()

        archive = BlockingArchive()

        # Start drain_spool in background
        drain_task = asyncio.create_task(drain_spool(archive, spool_path))

        # Wait for drain to start and block on the first entry
        await asyncio.sleep(0.01)

        # While drain is blocked, append a second event via record_lossless
        # Use a failing archive so it goes to spool
        failing_archive = _FakeArchive(should_fail=True)
        await record_lossless(
            failing_archive,
            "tool_call",
            {"tool": "file_write"},
            agent_name="agent2",
            summary="file_write",
            spool_path=spool_path,
        )

        # Now release the drain
        drain_event.set()
        await drain_task

        # The spool should still contain the second event
        assert spool_path.exists()
        lines = spool_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event_type"] == "tool_call"
        assert entry["data"]["tool"] == "file_write"


@pytest.mark.asyncio
async def test_record_lossless_non_json_payload_never_raises():
    """record_lossless handles non-JSON-serializable data without raising."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        archive = _FakeArchive(should_fail=True)

        import base64
        from datetime import datetime, timezone

        data = {
            "blob": b"\x00\xffraw",
            "when": datetime.now(timezone.utc),
        }

        # Must not raise
        await record_lossless(
            archive,
            "conversation",
            data,
            agent_name="agent1",
            summary="test",
            spool_path=spool_path,
        )

        # Spool must have one line
        assert spool_path.exists()
        lines = spool_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event_type"] == "conversation"
        assert entry["data"]["blob"]["__spool_type__"] == "bytes"
        assert entry["data"]["blob"]["b64"] == base64.b64encode(b"\x00\xffraw").decode()
        assert "when" in entry["data"]
        assert entry["data"]["when"]["__spool_type__"] == "datetime"


@pytest.mark.asyncio
async def test_concurrent_drains_do_not_double_replay():
    """Two concurrent drain_spool calls replay each entry exactly once."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        spool_path.parent.mkdir(parents=True, exist_ok=True)

        # Write three spooled entries
        entries = []
        for i in range(3):
            entries.append({
                "utc": f"2026-01-01T00:00:{i:02d}+00:00",
                "event_type": "conversation",
                "data": {"content": f"msg{i}"},
                "agent_name": "agent1",
                "summary": f"msg{i}",
                "error": "archive down",
            })
        spool_path.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
            encoding="utf-8",
        )

        # Recording fake archive that blocks until all drains have started
        start_barrier = asyncio.Barrier(2)
        done_event = asyncio.Event()

        class BlockingRecordingArchive:
            def __init__(self):
                self.calls = []

            async def record(self, event_type: str, data: dict, *, agent_name=None, summary=None):
                await start_barrier.wait()
                if not done_event.is_set():
                    await done_event.wait()
                self.calls.append({
                    "event_type": event_type,
                    "data": data,
                    "agent_name": agent_name,
                    "summary": summary,
                })

        archive = BlockingRecordingArchive()

        # Run two drains concurrently
        drain_tasks = [
            asyncio.create_task(drain_spool(archive, spool_path)),
            asyncio.create_task(drain_spool(archive, spool_path)),
        ]

        # Wait for both drains to hit the barrier (both have read the file)
        await asyncio.sleep(0.05)
        # Release them
        done_event.set()
        await asyncio.gather(*drain_tasks)

        # Each entry should be recorded exactly once
        assert len(archive.calls) == 3
        recorded_contents = {call["data"]["content"] for call in archive.calls}
        assert recorded_contents == {"msg0", "msg1", "msg2"}

        # Spool should be empty (all succeeded)
        assert not spool_path.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])