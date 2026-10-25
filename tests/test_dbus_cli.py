"""Tests for the dbus_cli busctl helper."""

from __future__ import annotations

import asyncio

import pytest

from tinyagentos import dbus_cli


@pytest.mark.asyncio
async def test_timeout_kills_process(monkeypatch):
    """On timeout, busctl should kill the child process and return rc=124."""
    kill_called = []
    waited = []

    class FakeProcess:
        def __init__(self):
            self.returncode = None

        async def communicate(self):
            # Never completes
            await asyncio.sleep(3600)
            return b"", b""

        def kill(self):
            kill_called.append(True)

        async def wait(self):
            waited.append(True)

    async def fake_create_subprocess_exec(*args, **kwargs):
        return FakeProcess()

    # Patch the timeout to a very small value for fast test
    monkeypatch.setattr(dbus_cli, "BUSCTL_TIMEOUT_S", 0.05)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    rc, data = await dbus_cli.busctl("call", "org.bluez", "/", "org.freedesktop.DBus.ObjectManager", "GetManagedObjects")

    assert rc == 124
    assert data is None
    assert kill_called, "proc.kill() should have been called"
    assert waited, "proc.wait() should have been called"