"""Unit tests for AppleContainerBackend (subprocess mocked)."""
from __future__ import annotations

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


import json


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
