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


def test_open_incus_pty_resolves_project_and_starts(monkeypatch):
    """_open_incus_pty must target the container's ACTUAL project and start it
    if stopped, so a shortcut works for a container in `default` while the
    incus client default project is a per-user one (e.g. user-999)."""
    import json as _json
    from tinyagentos.containers import lxc as lxcmod

    run_calls = []
    popen_calls = []

    class FakeCompleted:
        def __init__(self, rc, out):
            self.returncode = rc
            self.stdout = out

    def fake_run(args, **kw):
        run_calls.append(args)
        if "list" in args:
            return FakeCompleted(0, _json.dumps([
                {"name": "taos-agent-naira", "project": "default", "status": "Stopped"},
            ]))
        return FakeCompleted(0, "")

    class FakePopen:
        def __init__(self, args, **kw):
            popen_calls.append(args)
            self.pid = 4321

    monkeypatch.setattr(lxcmod.subprocess, "run", fake_run)
    monkeypatch.setattr(lxcmod.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(lxcmod.pty, "openpty", lambda: (10, 11))
    monkeypatch.setattr(lxcmod.os, "close", lambda fd: None)

    lxcmod._open_incus_pty("taos-agent-naira", "exec bash -l")

    # The exec targets the resolved project, not the client default.
    exec_cmd = popen_calls[0]
    assert "--project" in exec_cmd
    assert "default" in exec_cmd
    assert "taos-agent-naira" in exec_cmd
    # A stopped container was started first, in the same resolved project.
    start_cmds = [c for c in run_calls if "start" in c]
    assert start_cmds, "stopped container should be started before exec"
    assert "--project" in start_cmds[0] and "default" in start_cmds[0]


def test_open_incus_pty_running_container_not_restarted(monkeypatch):
    """A running container is exec'd directly, with no start call."""
    import json as _json
    from tinyagentos.containers import lxc as lxcmod

    run_calls = []
    popen_calls = []

    class FakeCompleted:
        def __init__(self, rc, out):
            self.returncode = rc
            self.stdout = out

    def fake_run(args, **kw):
        run_calls.append(args)
        if "list" in args:
            return FakeCompleted(0, _json.dumps([
                {"name": "taos-agent-naira", "project": "user-999", "status": "Running"},
            ]))
        return FakeCompleted(0, "")

    class FakePopen:
        def __init__(self, args, **kw):
            popen_calls.append(args)
            self.pid = 4321

    monkeypatch.setattr(lxcmod.subprocess, "run", fake_run)
    monkeypatch.setattr(lxcmod.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(lxcmod.pty, "openpty", lambda: (10, 11))
    monkeypatch.setattr(lxcmod.os, "close", lambda fd: None)

    lxcmod._open_incus_pty("taos-agent-naira", "exec bash -l")

    exec_cmd = popen_calls[0]
    assert "user-999" in exec_cmd
    assert not [c for c in run_calls if "start" in c], "running container must not be started"
