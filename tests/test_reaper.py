"""Tests for the reaper — hung executor.sh lane detection.

See tinyagentos/scheduling/reaper.py for the implementation.
"""

from __future__ import annotations

import time
import psutil

import pytest

from tinyagentos.scheduling.reaper import reap_hung_executor_sh


class _MockProc:
    """Mock a psutil.Process returned by process_iter.

    The reaper code accesses:
      - proc.info["cmdline"]   → list of cmdline args
      - proc.info["create_time"] → float (Unix epoch)
      - proc.ppid()            → parent process ID (via method)
      - proc.info["pid"]       → process PID
      - proc.kill()            → kill the process
      - proc.wait()            → wait for the process
    """
    __slots__ = ("info", "kill_called", "wait_called")

    def __init__(self, info: dict):
        self.info = info
        self.kill_called = False
        self.wait_called = False

    def ppid(self) -> int:
        return self.info["ppid"]

    def kill(self) -> None:
        """Mock kill — record call."""
        self.kill_called = True

    def wait(self, timeout: int | None = None) -> None:
        """Mock wait — record call."""
        self.wait_called = True


class _MockPI:
    """Mock psutil.process_iter that yields our controlled processes."""
    def __init__(self, processes: list[_MockProc]):
        self._processes = processes

    def __iter__(self):
        return iter(self._processes)


def _make_mock_process(create_time: float, cmdline_parts: list, ppid: int = None) -> _MockProc:
    """Create a mock process info object."""
    import os
    pid = os.getpid() + hash(tuple(cmdline_parts)) % 10000 + 1000  # uniqueish
    actual_ppid = ppid if ppid is not None else max(os.getppid(), 1)
    info = {
        "pid": pid,
        "cmdline": cmdline_parts,
        "ppid": actual_ppid,
        "create_time": create_time,
        "name": "executor.sh",
    }
    return _MockProc(info=info)


@pytest.mark.asyncio
async def test_reap_hung_executor_sh_ages_past_cap(monkeypatch):
    """PROVE IT RED: an executor.sh aged past CAP gets reaped.

    We monkeypatch psutil.process_iter to return a mock old process.
    The reaper should kill it and return its info.
    """
    proc = _make_mock_process(create_time=time.time() - 3600, cmdline_parts=["executor.sh", "sleep", "3600"])

    original_iter = psutil.process_iter

    def mock_iter(attrs=None):
        return _MockPI([proc])

    monkeypatch.setattr(psutil, "process_iter", mock_iter)

    try:
        reaped = reap_hung_executor_sh(cap_seconds=300)  # 5 min cap
        assert len(reaped) == 1
        assert reaped[0]["pid"] == proc.info["pid"]
        assert reaped[0]["age"] > 300
        assert "executor.sh" in reaped[0]["cmdline"]
        assert proc.kill_called
        assert proc.wait_called
    finally:
        monkeypatch.setattr(psutil, "process_iter", original_iter)


@pytest.mark.asyncio
async def test_reap_hung_executor_sh_younger_than_cap_survives(monkeypatch):
    """PROVE IT DOES NOT OVER-REAP: a fresh executor.sh under the cap survives."""
    proc = _make_mock_process(create_time=time.time() - 10, cmdline_parts=["executor.sh", "sleep", "3600"])

    original_iter = psutil.process_iter

    def mock_iter(attrs=None):
        return _MockPI([proc])

    monkeypatch.setattr(psutil, "process_iter", mock_iter)

    try:
        reaped = reap_hung_executor_sh(cap_seconds=300)
        assert len(reaped) == 0
        assert not proc.kill_called
        assert not proc.wait_called
    finally:
        monkeypatch.setattr(psutil, "process_iter", original_iter)


@pytest.mark.asyncio
async def test_reap_hung_executor_sh_cross_agent_guard(monkeypatch):
    """Cross-agent guard: an orphaned executor.sh (PPID 1) is NOT reaped.

    The guard should skip processes whose parent is PPID 1 (orphaned into init).
    """
    proc = _make_mock_process(create_time=time.time() - 3600, cmdline_parts=["executor.sh", "sleep", "3600"], ppid=1)

    original_iter = psutil.process_iter

    def mock_iter(attrs=None):
        return _MockPI([proc])

    monkeypatch.setattr(psutil, "process_iter", mock_iter)

    try:
        reaped = reap_hung_executor_sh(cap_seconds=300)
        # Orphaned process (PPID 1) should NOT be reaped
        assert len(reaped) == 0
        assert not proc.kill_called
        assert not proc.wait_called
    finally:
        monkeypatch.setattr(psutil, "process_iter", original_iter)


def test_reaper_module_importable():
    """Smoke test that the reaper module can be imported and the function exists."""
    assert callable(reap_hung_executor_sh)


@pytest.mark.asyncio
async def test_reap_hung_executor_sh_live_parent_gets_reaped(monkeypatch):
    """PROVE IT RED: an executor.sh with a live parent (not PPID 1) aged past CAP gets reaped.

    This covers the bug scenario: the hung lane's parent is dispatch_loop (alive, not PPID 1),
    not an agent CLI and not orphaned into init. The reaper's new third rule catches it.
    """
    proc = _make_mock_process(
        create_time=time.time() - 3600,
        cmdline_parts=["executor.sh", "sleep", "3600"],
        ppid=1234,
    )

    original_iter = psutil.process_iter

    def mock_iter(attrs=None):
        return _MockPI([proc])

    monkeypatch.setattr(psutil, "process_iter", mock_iter)

    try:
        reaped = reap_hung_executor_sh(cap_seconds=300)
        assert len(reaped) == 1
        assert reaped[0]["pid"] == proc.info["pid"]
        assert reaped[0]["age"] > 300
        assert "executor.sh" in reaped[0]["cmdline"]
        assert proc.kill_called
        assert proc.wait_called
    finally:
        monkeypatch.setattr(psutil, "process_iter", original_iter)