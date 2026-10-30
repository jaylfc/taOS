"""Tests for the dbus_cli busctl helper."""

from __future__ import annotations

import asyncio
import os

import pytest

from tinyagentos import dbus_cli


@pytest.mark.asyncio
async def test_timeout_kills_process(monkeypatch):
    """On timeout, busctl should kill the child process and return rc=124."""
    kill_called = []
    communicated = []

    class FakeProcess:
        def __init__(self):
            self.returncode = None
            self._killed = False

        async def communicate(self):
            communicated.append(True)
            if self._killed:
                return b"", b""
            # Never completes if not killed
            await asyncio.sleep(3600)
            return b"", b""

        def kill(self):
            kill_called.append(True)
            self._killed = True

        async def wait(self):
            raise AssertionError("busctl() must drain with communicate() after kill, not wait()")

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProcess()

    # Patch the timeout to a very small value for fast test
    monkeypatch.setattr(dbus_cli, "BUSCTL_TIMEOUT_S", 0.05)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    rc, data = await dbus_cli.busctl("call", "org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")

    assert rc == 124
    assert data is None
    assert kill_called, "proc.kill() should have been called"
    assert communicated, "proc.communicate() should have been called"


@pytest.mark.asyncio
async def test_cancel_kills_and_reaps_process(monkeypatch):
    """On cancellation, busctl should kill the child process and re-raise."""
    kill_called = []
    communicated = []

    class FakeProcess:
        def __init__(self):
            self.returncode = None
            self._killed = False

        async def communicate(self):
            communicated.append(True)
            if self._killed:
                return b"", b""
            # Never completes if not killed
            await asyncio.sleep(3600)
            return b"", b""

        def kill(self):
            kill_called.append(True)
            self._killed = True

        async def wait(self):
            raise AssertionError("busctl() must drain with communicate() after kill, not wait()")

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProcess()

    # Patch the timeout to a very small value for fast test
    monkeypatch.setattr(dbus_cli, "BUSCTL_TIMEOUT_S", 0.05)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    task = asyncio.create_task(dbus_cli.busctl("call", "org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"))
    # Give it a moment to start waiting
    await asyncio.sleep(0)
    # Cancel the task
    task.cancel()
    
    with pytest.raises(asyncio.CancelledError):
        await task
    
    assert kill_called, "proc.kill() should have been called on cancellation"
    assert communicated, "proc.communicate() should have been called on cancellation"


@pytest.mark.asyncio
async def test_cancel_before_spawn_propagates_cancelled(monkeypatch):
    """If cancellation lands before create_subprocess_exec returns, CancelledError must propagate (no UnboundLocalError)."""
    started = asyncio.Event()

    class FakeProcess:
        def __init__(self):
            self.returncode = None

        async def communicate(self):
            # Should not be called since proc is None
            await asyncio.sleep(3600)
            return b"", b""

        def kill(self):
            pass

        async def wait(self):
            pass

    async def fake_create_subprocess_exec(*args, **kwargs):
        # Signal that we've been called, then never return
        started.set()
        await asyncio.Event().wait()
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    task = asyncio.create_task(dbus_cli.busctl("call", "org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"))
    # Wait for the fake to be entered
    await started.wait()
    # Yield control so the task is at the await point
    await asyncio.sleep(0)
    # Cancel before create_subprocess_exec returns
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cancel_with_flooding_child_propagates(tmp_path, monkeypatch):
    """Cancelling busctl with a child that floods stdout must propagate CancelledError, not hang."""
    busctl_script = tmp_path / "busctl"
    busctl_script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "while True:\n"
        "    sys.stdout.write('x' * 65536)\n"
        "    sys.stdout.flush()\n"
    )
    busctl_script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    task = asyncio.create_task(dbus_cli.busctl("x"))
    await asyncio.sleep(0.5)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(asyncio.shield(task), timeout=5)


@pytest.mark.asyncio
async def test_timeout_with_flooding_child_returns_124(tmp_path, monkeypatch):
    """busctl timeout with a child that floods stdout must return (124, None), not hang."""
    busctl_script = tmp_path / "busctl"
    busctl_script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "while True:\n"
        "    sys.stdout.write('x' * 65536)\n"
        "    sys.stdout.flush()\n"
    )
    busctl_script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    monkeypatch.setattr(dbus_cli, "BUSCTL_TIMEOUT_S", 0.5)

    assert await asyncio.wait_for(dbus_cli.busctl("x"), timeout=6) == (124, None)


class _FakeProcess:
    """Helper to create a fake subprocess with configurable returncode and output."""

    def __init__(self, returncode: int, stdout: bytes, stderr: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.kill_called = False

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self):
        self.kill_called = True

    async def wait(self):
        pass


@pytest.mark.asyncio
async def test_busctl_empty_stdout_on_success_returns_zero(monkeypatch):
    """rc=0 with empty stdout should return (0, None), not (1, None)."""
    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProcess(returncode=0, stdout=b"")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    rc, data = await dbus_cli.busctl("set-property", "a", "/b", "c", "D", "b", "true")
    assert rc == 0
    assert data is None


@pytest.mark.asyncio
async def test_busctl_whitespace_stdout_on_success_returns_zero(monkeypatch):
    """rc=0 with whitespace-only stdout should return (0, None)."""
    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProcess(returncode=0, stdout=b"   \n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    rc, data = await dbus_cli.busctl("call", "org.freedesktop.systemd1", "/org/freedesktop/systemd1", "org.freedesktop.DBus.Peer", "Ping")
    assert rc == 0
    assert data is None


@pytest.mark.asyncio
async def test_busctl_invalid_json_on_success_returns_one(monkeypatch):
    """rc=0 with non-JSON stdout should still return (1, None) (guards against over-widening)."""
    async def fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProcess(returncode=0, stdout=b"not json")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    rc, data = await dbus_cli.busctl("call", "org.example", "/path", "iface", "Method")
    assert rc == 1
    assert data is None


@pytest.mark.asyncio
async def test_second_cancel_during_drain_still_drains(monkeypatch):
    """A second cancellation during post-kill drain must not interrupt the drain; pipes must still be reaped."""
    drain_gate = asyncio.Event()
    drained = []

    class FakeProcess:
        def __init__(self):
            self.returncode = None
            self._killed = False

        async def communicate(self):
            if self._killed:
                # Post-kill drain waits for the gate, then marks drained
                await drain_gate.wait()
                drained.append(True)
                return b"", b""
            # First communicate() call never returns unless killed
            await asyncio.sleep(3600)
            return b"", b""

        def kill(self):
            self._killed = True

        async def wait(self):
            raise AssertionError("busctl() must drain with communicate() after kill, not wait()")

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProcess()

    monkeypatch.setattr(dbus_cli, "BUSCTL_TIMEOUT_S", 0.05)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    task = asyncio.create_task(dbus_cli.busctl("call", "org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects"))
    # Let the task start and hit communicate()
    await asyncio.sleep(0)
    # First cancellation: triggers kill + drain
    task.cancel()
    # Let the cancellation propagate to the kill/drain point
    await asyncio.sleep(0)
    # Second cancellation: must not interrupt the drain
    task.cancel()
    # Now allow the drain to complete
    drain_gate.set()
    # The task must still raise CancelledError
    with pytest.raises(asyncio.CancelledError):
        await task
    # But the drain must have completed
    assert drained, "proc.communicate() drain must complete even after second cancellation"
