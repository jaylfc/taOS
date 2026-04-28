"""Unit tests for AppleContainerBackend (subprocess mocked)."""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, patch

import pytest


def _backend(monkeypatch, bin_path: str | None = None):
    from tinyagentos.containers.apple_backend import AppleContainerBackend
    if bin_path is None:
        monkeypatch.delenv("TAOS_CONTAINER_BIN", raising=False)
    else:
        monkeypatch.setenv("TAOS_CONTAINER_BIN", bin_path)
    return AppleContainerBackend()


def test_resolves_cli_from_env(monkeypatch):
    b = _backend(monkeypatch, "/Applications/taOS.app/Contents/Resources/bin/container")
    assert b.binary == "/Applications/taOS.app/Contents/Resources/bin/container"


def test_falls_back_to_path(monkeypatch):
    b = _backend(monkeypatch)
    assert b.binary == "container"


@pytest.mark.asyncio
async def test_run_invokes_subprocess(monkeypatch):
    b = _backend(monkeypatch, "/usr/local/bin/container")

    async def fake_exec(*cmd, **kwargs):
        class P:
            returncode = 0
            async def communicate(self):
                return (b"hello", b"")
        return P()

    with patch("asyncio.create_subprocess_exec", side_effect=fake_exec) as m:
        code, out = await b._run([b.binary, "ls"])
        assert code == 0
        assert out == "hello"
        # First positional arg is the binary
        assert m.call_args.args[0] == "/usr/local/bin/container"


@pytest.mark.asyncio
async def test_list_containers_filters_by_prefix(monkeypatch):
    b = _backend(monkeypatch)
    payload = json.dumps([
        {"name": "taos-agent-alice", "status": "running",
         "ip": "192.168.65.3", "memory": "2GB", "cpus": 2},
        {"name": "other-thing", "status": "running",
         "ip": "192.168.65.4", "memory": "1GB", "cpus": 1},
    ])
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, payload)
        items = await b.list_containers()

    assert len(items) == 1
    assert items[0].name == "taos-agent-alice"
    assert items[0].status == "running"
    assert items[0].ip == "192.168.65.3"
    assert items[0].memory_mb == 2048
    assert items[0].cpu_cores == 2


@pytest.mark.asyncio
async def test_list_containers_returns_empty_on_failure(monkeypatch):
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (1, "container daemon not running")
        items = await b.list_containers()
    assert items == []


@pytest.mark.asyncio
async def test_list_containers_returns_empty_on_bad_json(monkeypatch):
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, "not json {{{")
        items = await b.list_containers()
    assert items == []


@pytest.mark.asyncio
async def test_create_container_builds_argv(monkeypatch):
    b = _backend(monkeypatch, "/usr/local/bin/container")
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, "abc123")
        result = await b.create_container(
            name="taos-agent-bob",
            image="docker.io/library/debian:bookworm",
            memory_limit="2GB",
            cpu_limit=2,
            mounts=[("/host/data", "/data")],
            env={"FOO": "bar"},
        )

    assert result["success"] is True
    argv = m.call_args.args[0]
    assert argv[0] == "/usr/local/bin/container"
    assert argv[1] == "run"
    assert "-d" in argv
    assert "--name" in argv and "taos-agent-bob" in argv
    assert "--memory" in argv and "2g" in argv
    assert "--cpus" in argv and "2" in argv
    assert "-v" in argv and "/host/data:/data" in argv
    assert "-e" in argv and "FOO=bar" in argv
    assert "docker.io/library/debian:bookworm" in argv


@pytest.mark.asyncio
async def test_create_container_returns_failure(monkeypatch):
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (125, "image not found")
        result = await b.create_container(name="taos-agent-bad", image="nonexistent")
    assert result["success"] is False
    assert "image not found" in result["output"]


@pytest.mark.asyncio
async def test_create_container_with_root_size_gib_does_not_orphan(monkeypatch):
    """set_root_quota returns accounting-only success; create_container still succeeds."""
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, "abc123")
        result = await b.create_container(
            name="taos-agent-quota",
            image="docker.io/library/debian:bookworm",
            root_size_gib=10,
        )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_exec_in_container(monkeypatch):
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, "hello world\n")
        code, output = await b.exec_in_container("taos-agent-x", ["echo", "hi"])
    assert code == 0
    assert "hello" in output
    argv = m.call_args.args[0]
    assert argv[1] == "exec"
    assert "taos-agent-x" in argv
    assert "echo" in argv and "hi" in argv


@pytest.mark.asyncio
async def test_push_file(monkeypatch):
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, "")
        code, output = await b.push_file("taos-agent-x", "/tmp/foo", "/etc/foo")
    argv = m.call_args.args[0]
    assert argv[1] == "cp"
    assert "/tmp/foo" in argv
    assert "taos-agent-x:/etc/foo" in argv


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,verb,extra",
    [
        ("start_container", "start", {}),
        ("restart_container", "restart", {}),
        ("destroy_container", "rm", {}),
    ],
)
async def test_lifecycle_simple(monkeypatch, method, verb, extra):
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, "ok")
        result = await getattr(b, method)("taos-agent-x", **extra)
    argv = m.call_args.args[0]
    assert argv[1] == verb
    assert "taos-agent-x" in argv
    assert result["success"] is True


@pytest.mark.asyncio
async def test_stop_uses_kill_when_force(monkeypatch):
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, "ok")
        await b.stop_container("x", force=True)
    assert m.call_args.args[0][1] == "kill"

    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, "ok")
        await b.stop_container("x", force=False)
    assert m.call_args.args[0][1] == "stop"


@pytest.mark.asyncio
async def test_destroy_force_removes_running(monkeypatch):
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, "ok")
        await b.destroy_container("x")
    argv = m.call_args.args[0]
    assert "-f" in argv  # rm -f to remove running containers


@pytest.mark.asyncio
async def test_get_container_logs(monkeypatch):
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, "line1\nline2\n")
        out = await b.get_container_logs("x", lines=42)
    argv = m.call_args.args[0]
    assert argv[1] == "logs"
    assert "--tail" in argv and "42" in argv
    assert "x" in argv
    assert "line1" in out


@pytest.mark.asyncio
async def test_rename_container(monkeypatch):
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, "")
        result = await b.rename_container("old", "new")
    argv = m.call_args.args[0]
    assert argv[1] == "rename"
    assert argv[-2:] == ["old", "new"]
    assert result["success"] is True


@pytest.mark.asyncio
async def test_get_container_logs_returns_error_message_on_failure(monkeypatch):
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (1, "no such container: x")
        out = await b.get_container_logs("x")
    assert out.startswith("Error getting logs:")
    assert "no such container" in out


@pytest.mark.asyncio
async def test_snapshot_create(monkeypatch):
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, "sha256:deadbeef")
        result = await b.snapshot_create("x", "v1")
    argv = m.call_args.args[0]
    assert argv[1] == "commit"
    assert "x" in argv
    assert "taos/v1:latest" in argv
    assert result["success"] is True


@pytest.mark.asyncio
async def test_snapshot_restore_unsupported(monkeypatch):
    b = _backend(monkeypatch)
    result = await b.snapshot_restore("x", "v1")
    assert result["success"] is False
    assert "not supported" in result["note"]


@pytest.mark.asyncio
async def test_snapshot_list_parses_taos_images(monkeypatch):
    b = _backend(monkeypatch)
    payload = '[{"reference":"taos/v1:latest"},{"reference":"taos/v2:latest"}]'
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, payload)
        result = await b.snapshot_list("x")
    assert result["success"] is True
    assert result["snapshots"] == ["v1", "v2"]


@pytest.mark.asyncio
async def test_snapshot_create_failure(monkeypatch):
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (1, "no such container: x")
        result = await b.snapshot_create("x", "v1")
    assert result["success"] is False
    assert "no such container" in result["output"]


@pytest.mark.asyncio
async def test_snapshot_list_returns_failure_on_nonzero_exit(monkeypatch):
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (1, "images daemon error")
        result = await b.snapshot_list("x")
    assert result["success"] is False
    assert result["snapshots"] == []


@pytest.mark.asyncio
async def test_snapshot_list_returns_failure_on_bad_json(monkeypatch):
    b = _backend(monkeypatch)
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, "not json {{{")
        result = await b.snapshot_list("x")
    assert result["success"] is False
    assert result["snapshots"] == []


@pytest.mark.asyncio
async def test_snapshot_list_excludes_non_taos_images(monkeypatch):
    b = _backend(monkeypatch)
    payload = (
        '[{"reference":"taos/v1:latest"},'
        '{"reference":"library/debian:bookworm"},'
        '{"reference":"taos/v2:latest"}]'
    )
    with patch.object(b, "_run", new_callable=AsyncMock) as m:
        m.return_value = (0, payload)
        result = await b.snapshot_list("x")
    assert result["success"] is True
    assert result["snapshots"] == ["v1", "v2"]


@pytest.mark.asyncio
async def test_add_proxy_device_is_noop(monkeypatch):
    b = _backend(monkeypatch)
    result = await b.add_proxy_device("x", "litellm", "tcp:127.0.0.1:4000",
                                       "tcp:127.0.0.1:4000")
    assert result["success"] is True
    assert "host.containers.internal" in result["note"]


@pytest.mark.asyncio
async def test_set_env_unsupported(monkeypatch):
    b = _backend(monkeypatch)
    result = await b.set_env("x", "KEY", "value")
    assert result["success"] is False
    assert "recreate" in result["note"]


@pytest.mark.asyncio
async def test_set_root_quota_accounting_only(monkeypatch):
    b = _backend(monkeypatch)
    result = await b.set_root_quota("x", 5)
    assert result["success"] is True
    assert "accounting-only" in result["note"]
