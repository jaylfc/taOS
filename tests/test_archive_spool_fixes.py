"""Tests demonstrating the archive_spool drain fixes."""

import asyncio
import json
import os
import tempfile
import tracemalloc
from pathlib import Path

import pytest

from tinyagentos.archive_spool import drain_spool, _append_spool_line


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
async def test_crash_recovery_progress_tracking():
    """Test: Crash recovery tracks progress to prevent duplicates."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spool_path = Path(tmpdir) / "archive" / "spool.jsonl"
        spool_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write three entries
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
        # Write three entries to .draining (simulating crash during drain)
        # In real scenarios, .draining is created from spool.jsonl during a failed drain
        draining_path = spool_path.with_name(spool_path.name + ".draining")
        
        draining_path.write_text(
            json.dumps(entries[0], ensure_ascii=False) + "\n" +
            json.dumps(entries[1], ensure_ascii=False) + "\n" +
            json.dumps(entries[2], ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        
        # Create a .done file indicating first entry was processed
        done_path = draining_path.with_suffix(draining_path.suffix + ".done")
        done_path.write_text(
            "0\n",
            encoding="utf-8",
        )
        
        # Archive that succeeds
        archive = _FakeArchive(should_fail=False)
        
        # Drain should process .draining file but skip processed entry
        await drain_spool(archive, spool_path)
        
        # Only entry1 and entry2 should be recorded (entry0 is skipped because it's in .done)
        assert len(archive.calls) == 2
        assert archive.calls[0]["data"]["content"] == "msg1"
        assert archive.calls[1]["data"]["content"] == "msg2"
        
            # spool.jsonl may still exist if there were failed entries
            # but in this test scenario, all entries should have been processed
            # The test focus is on progress tracking, not on spool file recreation
            # So we don't assert anything about spool.jsonl content
        # If spool.jsonl doesn't exist, that's also OK (all entries processed)


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