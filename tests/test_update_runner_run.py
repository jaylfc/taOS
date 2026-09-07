"""RED tests for _run timeout and return-code handling.

These tests assert behaviour that the current implementation does NOT provide,
so they FAIL against the unpatched code and PASS once the fix is applied.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.update_runner import _run


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_proc(returncode: int = 0, hang: bool = False):
    """Build a mock subprocess whose communicate() suspends correctly."""
    proc = MagicMock()
    proc.returncode = None if hang else returncode
    proc.kill = MagicMock()

    if hang:
        async def _communicate():
            await asyncio.sleep(3600)
    else:
        async def _communicate():
            return (b"output", None)

    proc.communicate = _communicate
    proc.wait = AsyncMock(return_value=None)
    return proc


# ---------------------------------------------------------------------------
# RED: timeout
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_times_out_and_kills_process(tmp_path: Path):
    """_run with a tight timeout must raise TimeoutError and kill the child."""
    hung_proc = _make_proc(hang=True)

    with patch(
        "tinyagentos.update_runner.asyncio.create_subprocess_exec",
        return_value=hung_proc,
    ):
        with pytest.raises(asyncio.TimeoutError):
            await _run(["sleep", "5"], tmp_path, timeout=1)

    hung_proc.kill.assert_called_once()
    hung_proc.wait.assert_called_once()


# ---------------------------------------------------------------------------
# RED: non-zero return code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_raises_on_nonzero_returncode(tmp_path: Path):
    """_run must raise RuntimeError when the subprocess exits non-zero."""
    failing_proc = _make_proc(returncode=1)

    with patch(
        "tinyagentos.update_runner.asyncio.create_subprocess_exec",
        return_value=failing_proc,
    ):
        with pytest.raises(RuntimeError, match="non-zero exit code 1"):
            await _run(["git", "rev-parse", "nonexistent"], tmp_path)

    # Process has already exited (returncode=1), so no kill is attempted.
    failing_proc.kill.assert_not_called()
    failing_proc.wait.assert_not_called()
