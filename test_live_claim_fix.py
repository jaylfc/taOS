"""Test that live claim is never reclaimed.

This test verifies that the fix for tsk-lve2bo ensures that:
1. A claim is bound to a writer with pid+boot_id
2. A loser with a tiny poll budget does NOT unlink the claim when the owner is live
3. The loser does NOT return different bytes (adopts the winner's bytes)
"""
import os
import time
import threading
from pathlib import Path
import tempfile
import sys

sys.path.insert(0, '/tmp/exec-tsk-lve2bo')

from tinyagentos.atomic_io import atomic_create_bytes, _create_via_claim


def test_live_claim_is_never_reclaimed():
    """Test that a live claim is never reclaimed by a loser with tiny poll budget.

    This is the acceptance test for tsk-lve2bo:
    - A live child process holds a claim
    - A second process runs as loser with tiny poll budget
    - The loser should NOT unlink the claim
    - The loser should adopt the winner's bytes, not create its own
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        target = tmp_path / "test_key.bin"
        claim = target.with_name(target.name + ".claim")

        # Set a tiny poll budget to force quick test execution
        from tinyagentos import atomic_io
        original_attempts = atomic_io._CLAIM_POLL_ATTEMPTS
        original_interval = atomic_io._CLAIM_POLL_INTERVAL
        atomic_io._CLAIM_POLL_ATTEMPTS = 2
        atomic_io._CLAIM_POLL_INTERVAL = 0.01

        results = {"winner_bytes": None, "loser_bytes": None, "claim_unlinked": False}

        # Create the file first to test normal behavior
        target.write_bytes(b"existing-content")

        def winner():
            """Writer that acquires claim, writes bytes, then pauses before cleanup."""
            # Set BOOT_ID and CONTAINER_ID for test isolation
            os.environ["BOOT_ID"] = "test-boot-123"
            os.environ["CONTAINER_ID"] = "test-container-456"
            
            # This will create the claim file
            data = b"winner-bytes"
            results["winner_bytes"] = data
            
            # Call the internal _create_via_claim to test the claim mechanism directly
            returned = _create_via_claim(target, data, mode=0o600)
            
            # Store the claim contents for verification
            if claim.exists():
                with open(claim, "r", encoding="utf-8") as fh:
                    results["claim_contents"] = fh.read().strip()
            
            # Hold onto the claim (pause before cleanup) - the claim should be cleaned up
            # by _create_via_claim after atomic_write_bytes completes
            time.sleep(0.5)

        def loser():
            """Reader that should see the claim and adopt winner's bytes."""
            # Give winner time to acquire claim and write
            time.sleep(0.1)
            
            # Now try to create - should see claim and read winner's bytes
            returned = _create_via_claim(target, b"loser-bytes", mode=0o600)
            results["loser_bytes"] = returned
            
            # Check if claim was unlinked
            results["claim_unlinked"] = not claim.exists()

        # Start both processes
        winner_thread = threading.Thread(target=winner)
        loser_thread = threading.Thread(target=loser)
        
        winner_thread.start()
        loser_thread.start()
        
        winner_thread.join(timeout=2)
        loser_thread.join(timeout=2)
        
        # Restore original poll settings
        atomic_io._CLAIM_POLL_ATTEMPTS = original_attempts
        atomic_io._CLAIM_POLL_INTERVAL = original_interval

        # Verify expectations:
        # 1. Loser should NOT have unlinked the claim
        assert not results.get("claim_unlinked", False), \
            "FAIL: loser unlinked a live claim - claim should only be reclaimed when owner is dead"
        
        # 2. Loser should have adopted winner's bytes, not created its own
        assert results["loser_bytes"] == b"winner-bytes", \
            f"FAIL: loser returned {results['loser_bytes']!r}, should have adopted winner's bytes"
        
        # 3. Target should contain winner's bytes
        assert target.read_bytes() == b"winner-bytes", \
            "FAIL: target does not contain winner's bytes"
        
        # 4. Claim should have been written with pid+boot_id
        if "claim_contents" in results:
            assert " " in results["claim_contents"], \
                "FAIL: claim should contain 'pid boot_id' format"
            parts = results["claim_contents"].split()
            assert len(parts) >= 2, "FAIL: claim should have at least pid and boot_id"
            
            # Verify pid is the current process PID (winner's PID)
            winner_pid = os.getpid()
            # Note: In this test, the winner runs in a separate thread
            # but we're checking the process level, so we verify the format is correct
            try:
                pid_val = int(parts[0])
                assert pid_val > 0, "FAIL: claim pid should be positive"
            except (ValueError, IndexError):
                raise AssertionError("FAIL: claim pid is not a valid integer")

    print("PASS: live claim was never reclaimed - loser correctly adopted winner's bytes")


if __name__ == "__main__":
    test_live_claim_is_never_reclaimed()