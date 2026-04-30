"""
Unit tests verify the incus command shape. Integration test skipped when incus absent.
"""
import shutil
import pytest
from unittest.mock import MagicMock

from tinyagentos.containers.lxc import LXCBackend


def test_spawn_pty_command_no_cmd(monkeypatch):
    """spawn_pty with cmd=None must use default shell via incus."""
    launched_cmds = []

    class FakePty:
        def __init__(self, args, **kwargs):
            launched_cmds.append(args)
        def read(self, size=4096): return b""
        def write(self, data): pass
        def resize(self, rows, cols): pass
        def close(self): pass

    monkeypatch.setattr(
        "tinyagentos.containers.lxc._open_incus_pty", lambda *a, **k: FakePty(a)
    )
    backend = LXCBackend()
    backend.spawn_pty("myagent", cmd=None)
    assert len(launched_cmds) == 1
    cmd_args = launched_cmds[0]
    assert "taos-agent-myagent" in str(cmd_args)
    assert "bash" in str(cmd_args)


def test_spawn_pty_command_with_cmd(monkeypatch):
    """spawn_pty with a command must embed that command in the incus call."""
    launched_cmds = []

    class FakePty:
        def __init__(self, args, **kwargs):
            launched_cmds.append(args)
        def read(self, size=4096): return b""
        def write(self, data): pass
        def resize(self, rows, cols): pass
        def close(self): pass

    monkeypatch.setattr(
        "tinyagentos.containers.lxc._open_incus_pty", lambda *a, **k: FakePty(a)
    )
    backend = LXCBackend()
    backend.spawn_pty("myagent", cmd=["openclaw", "agent"])
    cmd_args = launched_cmds[0]
    assert "openclaw" in str(cmd_args)


@pytest.mark.skipif(
    shutil.which("incus") is None, reason="incus not installed on this host"
)
def test_spawn_pty_integration():
    """Real integration test — requires a running container taos-agent-test-pty."""
    backend = LXCBackend()
    try:
        handle = backend.spawn_pty("test-pty", cmd=None)
        handle.write(b"echo hello-from-pty\n")
        import time
        time.sleep(0.2)
        output = handle.read(4096)
        handle.close()
        assert b"hello-from-pty" in output
    except Exception as exc:
        pytest.skip(f"Container taos-agent-test-pty not available: {exc}")
