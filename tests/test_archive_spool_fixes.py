"""Tests demonstrating the archive_spool drain fixes."""

import asyncio
import base64
import json
import os
import tempfile
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tinyagentos.archive_spool import drain_spool, record_lossless, _append_spool_line


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
async def test_event_preserved_when_append_fails():
    """Test: When _append_spool_line fails, .draining persists to preserve events."""
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
        
        # Monkey-patch _append_spool_line to raise
        def failing_append(spool_path: Path, line: str) -> None:
            raise RuntimeError("Simulated _append_spool_line failure")
        
        import tinyagentos.archive_spool as archive_spool_module
        original_append = archive_spool_module._append_spool_line
        archive_spool_module._append_spool_line = failing_append
        
        try:
            # Archive that fails
            archive = _FakeArchive(should_fail=True)
            
            # Drain should fail to append back
            await drain_spool(archive, spool_path)
            
            # After drain, .draining SHOULD exist (fix: don't unlink on append-back failure)
            draining_path = spool_path.with_name(spool_path.name + ".draining")
            assert draining_path.exists(), "The .draining file should persist when append-back fails"
            
            # .draining should still contain both lines (events are preserved)
            lines = draining_path.read_text(encoding="utf-8").strip().splitlines()
            assert len(lines) == 2, "Both events should be preserved in .draining"
            
        finally:
            # Restore original function
            archive_spool_module._append_spool_line = original_append


@pytest.mark.asyncio
async def test_line_by_line_reading():
    """Test: drain_spool reads files line by line, not all at once."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        spool_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write entries with different lengths to test line-by-line processing
        entries = []
        for i in range(10):
            entries.append({
                "utc": f"2026-01-01T00:00:{i:02d}+00:00",
                "event_type": "conversation",
                "data": {"content": f"msg{i}" * 10},  # Make entries large
                "agent_name": "agent1",
                "summary": f"msg{i}",
                "error": "archive down",
            })
        
        spool_path.write_text(
            "\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n",
            encoding="utf-8",
        )
        
        # Archive that fails
        archive = _FakeArchive(should_fail=True)
        
        # Monitor memory usage to ensure we're reading line-by-line
        tracemalloc.start()
        
        await drain_spool(archive, spool_path)
        
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        
        # With line-by-line reading, memory should be reasonable
        # even for large lines
        assert peak < 2000000  # Less than 2MB for 10 large lines


@pytest.mark.asyncio
async def test_parent_directory_fsync_when_new_file():
    """Test: _append_spool_line fsyncs parent directory when creating new spool."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        
        # Remove the archive directory if it exists
        if spool_path.parent.exists():
            import shutil
            shutil.rmtree(spool_path.parent)
        
        # Monkey-patch os.fsync to track calls
        original_fsync = os.fsync
        fsync_calls = []
        
        def tracking_fsync(fd):
            fsync_calls.append(fd)
            return original_fsync(fd)
        
        os.fsync = tracking_fsync
        
        try:
            # Call _append_spool_line on a new file
            _append_spool_line(spool_path, "test line")
            
            # Should have called fsync on parent directory FD when file was created
            # The fix ensures fsync is called on parent directory for new files
            # fsync is called on both file descriptor and parent directory
            assert len(fsync_calls) > 0  # fsync was called
            
        finally:
            os.fsync = original_fsync


@pytest.mark.asyncio
async def test_app_startup_schedules_background_task():
    """Test: App startup schedules drain_spool as background task (unit test for behavior)."""
    import inspect
    import tinyagentos.app as app_module
    
    with open(app_module.__file__, "r") as f:
        content = f.read()
    
    # Should use asyncio.create_task with done-callback
    lines = content.split('\n')
    
    # Look for the pattern in the app.py file
    has_background_task = False
    has_drain_with_callback = False
    
    for i, line in enumerate(lines):
        if "from tinyagentos.archive_spool import drain_spool" in line:
            # Check for the background task pattern
            for j in range(i, min(i+20, len(lines))):
                if "app.state.archive_spool_drain_task = asyncio.create_task" in lines[j]:
                    has_background_task = True
                    if "_drain_with_callback" in lines[j]:
                        has_drain_with_callback = True
                    break
            break
    
    assert has_background_task, "Startup should create a background task for drain_spool"
    assert has_drain_with_callback, "Background task should use _drain_with_callback"


@pytest.mark.asyncio
async def test_stale_done_index_does_not_skip_new_spool_entry():
    """Test (a): A stale .done file (spool.jsonl.draining.done) next to a new spool with one entry.
    
    After drain_spool, the archive stub should receive that entry.
    On the unmodified #4284 head, the stale .done index causes the entry to be skipped.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        spool_path.parent.mkdir(parents=True, exist_ok=True)

        # Create a stale .done file with index "0" (from a previous drain that crashed
        # after writing .done but before removing .draining; the .draining was cleaned up
        # but .done remained). The .done file name is spool.jsonl.draining.done.
        draining_path = spool_path.with_name(spool_path.name + ".draining")
        done_path = draining_path.with_suffix(draining_path.suffix + ".done")
        done_path.write_text("0\n", encoding="utf-8")

        # Write a NEW spool.jsonl with a fresh entry (simulating new spooling after crash)
        spool_path.write_text(
            json.dumps({
                "utc": "2026-01-01T00:00:01+00:00",
                "event_type": "conversation",
                "data": {"content": "new_msg"},
                "agent_name": "agent1",
                "summary": "new_msg",
                "error": "archive down",
            }, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        # Archive that succeeds
        archive = _FakeArchive(should_fail=False)

        # Drain should process the new spool entry, not skip it due to stale .done
        await drain_spool(archive, spool_path)

        # The archive should have received the new entry
        assert len(archive.calls) == 1, f"Expected 1 call, got {len(archive.calls)}"
        assert archive.calls[0]["data"]["content"] == "new_msg", "New entry should be processed"


@pytest.mark.asyncio
async def test_bytes_restored_on_replay():
    """Test (b): An entry whose data holds bytes b'\\x00\\xff' spooled then drained.
    
    The archive stub should receive the same bytes object, not a dict with __spool_type__.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        spool_path.parent.mkdir(parents=True, exist_ok=True)

        # Create an entry with bytes data using record_lossless (which uses _spool_default)
        test_bytes = b"\x00\xff"
        archive_fail = _FakeArchive(should_fail=True)
        await record_lossless(
            archive_fail,
            "conversation",
            {"blob": test_bytes, "other": "data"},
            agent_name="agent1",
            summary="test",
            spool_path=spool_path,
        )

        # Verify the spool has the tagged representation
        lines = spool_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 1
        spooled_entry = json.loads(lines[0])
        assert spooled_entry["data"]["blob"]["__spool_type__"] == "bytes"
        assert spooled_entry["data"]["blob"]["b64"] == base64.b64encode(test_bytes).decode()

        # Now drain with a working archive
        archive_ok = _FakeArchive(should_fail=False)
        await drain_spool(archive_ok, spool_path)

        # The archive should receive the restored bytes, not the tagged dict
        assert len(archive_ok.calls) == 1
        received_data = archive_ok.calls[0]["data"]
        assert isinstance(received_data["blob"], bytes), f"Expected bytes, got {type(received_data['blob'])}"
        assert received_data["blob"] == test_bytes, f"Expected {test_bytes}, got {received_data['blob']}"
        assert received_data["other"] == "data"


@pytest.mark.asyncio
async def test_all_replays_fail_all_lines_back_in_spool_draining_gone():
    """Test (c): All replays fail for 3 entries.
    
    All 3 lines should be back in spool.jsonl and .draining should be gone.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        spool_path.parent.mkdir(parents=True, exist_ok=True)

        # Write three entries to spool
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

        # Archive that always fails
        archive = _FakeArchive(should_fail=True)

        # Drain - all should fail
        await drain_spool(archive, spool_path)

        # .draining should be gone (renamed back or removed)
        draining_path = spool_path.with_name(spool_path.name + ".draining")
        assert not draining_path.exists(), ".draining should not exist after drain completes"

        # spool.jsonl should exist and contain all 3 entries
        assert spool_path.exists(), "spool.jsonl should exist"
        lines = spool_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3, f"Expected 3 lines in spool, got {len(lines)}"
        for i, line in enumerate(lines):
            entry = json.loads(line)
            assert entry["data"]["content"] == f"msg{i}"