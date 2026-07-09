"""Tests for the node-local backend service manager
(tinyagentos/cluster/backend_services.py)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from tinyagentos.cluster import backend_services as bs


# --------------------------------------------------------------------------
# load_managed_backends
# --------------------------------------------------------------------------

def _write_manifest(root: Path, sid: str, manifest: dict) -> None:
    d = root / "services" / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.yaml").write_text(yaml.safe_dump(manifest))


def test_load_managed_backends_returns_only_managed(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "rkllama", {
        "id": "rkllama",
        "lifecycle": {
            "auto_manage": True, "unit": "rkllama.service", "scope": "system",
            "health": {"url": "http://localhost:7833/api/tags", "expect": '"models"'},
        },
    })
    _write_manifest(tmp_path, "rk-llama-cpp", {
        "id": "rk-llama-cpp", "lifecycle": {"auto_manage": False},
    })
    _write_manifest(tmp_path, "no-lifecycle", {"id": "no-lifecycle"})

    backends = bs.load_managed_backends(tmp_path)
    assert len(backends) == 1
    b = backends[0]
    assert b.id == "rkllama"
    assert b.unit == "rkllama.service"
    assert b.scope == "system"
    assert b.health_url == "http://localhost:7833/api/tags"
    assert b.health_expect == '"models"'


def test_load_skips_managed_without_unit_or_scope(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "broken", {
        "id": "broken", "lifecycle": {"auto_manage": True, "scope": "bogus"},
    })
    assert bs.load_managed_backends(tmp_path) == []


# --------------------------------------------------------------------------
# resolve_scope / unit_state / service_action  (mocked systemctl)
# --------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, returncode: int, stderr: bytes = b""):
        self.returncode = returncode
        self._stderr = stderr

    async def communicate(self):
        return (b"", self._stderr)

    async def wait(self):
        return self.returncode

    def kill(self):
        pass


@pytest.mark.asyncio
async def test_resolve_scope_prefers_manifest_scope(monkeypatch) -> None:
    calls: list[list[str]] = []

    async def fake_rc(args):
        calls.append(args)
        # unit exists in system scope only
        return 0 if "--user" not in args else 1

    monkeypatch.setattr(bs, "_rc", fake_rc)
    scope = await bs.resolve_scope("rkllama.service", prefer="system")
    assert scope == "system"
    # prefer=system means the first cat probe is the system (no --user) one
    assert "--user" not in calls[0]


@pytest.mark.asyncio
async def test_unit_state_reports_enabled_active(monkeypatch) -> None:
    monkeypatch.setenv("INVOCATION_ID", "test")
    monkeypatch.setattr(bs, "_rc", lambda args: _coro(0))
    state = await bs.unit_state("rkllama.service", prefer="system")
    assert state["installed"] is True
    assert state["enabled"] is True
    assert state["active"] is True
    assert state["scope"] == "system"


@pytest.mark.asyncio
async def test_unit_state_not_installed(monkeypatch) -> None:
    monkeypatch.setenv("INVOCATION_ID", "test")
    monkeypatch.setattr(bs, "_rc", lambda args: _coro(1))  # cat fails both scopes
    state = await bs.unit_state("ghost.service")
    assert state["installed"] is False


@pytest.mark.asyncio
async def test_service_action_restart_ok(monkeypatch) -> None:
    monkeypatch.setenv("INVOCATION_ID", "test")
    monkeypatch.setattr(bs, "_rc", lambda args: _coro(0))  # scope resolves
    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        lambda *a, **k: _coro(_FakeProc(0)))
    result = await bs.service_action("rkllama.service", "restart", prefer="system")
    assert result == {"unit": "rkllama.service", "ok": True, "scope": "system"}


@pytest.mark.asyncio
async def test_service_action_failure_captures_stderr(monkeypatch) -> None:
    monkeypatch.setenv("INVOCATION_ID", "test")
    monkeypatch.setattr(bs, "_rc", lambda args: _coro(0))
    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        lambda *a, **k: _coro(_FakeProc(1, b"Interactive authentication required")))
    result = await bs.service_action("qmd.service", "restart")
    assert result["ok"] is False
    assert "Interactive authentication required" in result["detail"]


@pytest.mark.asyncio
async def test_service_action_not_installed(monkeypatch) -> None:
    monkeypatch.setenv("INVOCATION_ID", "test")
    monkeypatch.setattr(bs, "_rc", lambda args: _coro(1))  # cat fails -> no scope
    result = await bs.service_action("ghost.service", "restart")
    assert result == {"unit": "ghost.service", "ok": False, "detail": "not installed"}


@pytest.mark.asyncio
async def test_service_action_rejects_bad_verb() -> None:
    with pytest.raises(ValueError):
        await bs.service_action("x.service", "nuke")


# --------------------------------------------------------------------------
# health_probe (mocked httpx)
# --------------------------------------------------------------------------

class _FakeResp:
    def __init__(self, status_code: int, text: str):
        self.status_code = status_code
        self.text = text


class _FakeClient:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        if self._exc:
            raise self._exc
        return self._resp


@pytest.mark.asyncio
async def test_health_probe_ok(monkeypatch) -> None:
    monkeypatch.setattr(bs.httpx, "AsyncClient",
                        lambda *a, **k: _FakeClient(_FakeResp(200, '{"models": []}')))
    assert await bs.health_probe("http://x/api/tags", '"models"') == {"ok": True}


@pytest.mark.asyncio
async def test_health_probe_missing_marker(monkeypatch) -> None:
    monkeypatch.setattr(bs.httpx, "AsyncClient",
                        lambda *a, **k: _FakeClient(_FakeResp(200, "{}")))
    r = await bs.health_probe("http://x/api/tags", '"models"')
    assert r["ok"] is False


@pytest.mark.asyncio
async def test_health_probe_connection_error(monkeypatch) -> None:
    monkeypatch.setattr(bs.httpx, "AsyncClient",
                        lambda *a, **k: _FakeClient(exc=RuntimeError("refused")))
    r = await bs.health_probe("http://x/api/tags", "")
    assert r["ok"] is False
    assert "refused" in r["detail"]


# --------------------------------------------------------------------------
# helper
# --------------------------------------------------------------------------

async def _coro(value):
    return value
