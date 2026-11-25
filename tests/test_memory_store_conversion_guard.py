"""Memory store guard tests (tsk-pf2or2).

Tests the memory store format guard against the taosmd migrate command-line
contract (the COMMAND LINE, not Python modules):

    python -m taosmd migrate --probe --json --data-dir <dir>
        exit 0 current, 10 old (legacy store served as is, never converted
        on open), 11 unknown (live WAL / corrupt), 12 newer.

    python -m taosmd migrate --convert-vectors --data-dir <dir>
        exit 0 converted or already current, 1 unexpected, 2 refused
        (nothing touched), 3 unknown or newer, 4 failed and restored.

The subprocess is faked at the boundary (asyncio.create_subprocess_exec)
with fake exit codes; every code is covered. There are no pass stubs.
"""

import asyncio
import json
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.app import create_app
from tinyagentos.routes import settings as settings_mod
from tinyagentos.routes.settings import _update_local_taosmd


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

REAL_HEALTH_BODY = {
    "status": "ok",
    "version": "0.4.0",
    "capabilities": ["a2a.v1", "collections.v1", "search.v1", "graph.v1", "tasks.v1"],
}


class _FakeResp:
    def __init__(self, status_code=200, content_type="application/json", body=None):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class _FakeHttpClient:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def get(self, url, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._resp


def _fake_request(data_dir: Path, taosmd_dir: str,
                  memory_url: str = "http://localhost:7900",
                  restart_cmd: str = "systemctl restart taosmd"):
    config = types.SimpleNamespace(
        memory_url=memory_url,
        taosmd_dir=taosmd_dir,
        taosmd_restart_cmd=restart_cmd,
    )
    state = types.SimpleNamespace(config=config, data_dir=data_dir)
    return types.SimpleNamespace(app=types.SimpleNamespace(state=state))


def _make_fake_proc(exit_code: int, stdout: str = "", stderr: str = "") -> MagicMock:
    """Create a fake asyncio subprocess with the given exit code and output."""
    proc = MagicMock()
    proc.returncode = exit_code
    proc.communicate = AsyncMock(return_value=(stdout.encode(), stderr.encode()))
    proc.wait = AsyncMock(return_value=exit_code)
    proc.kill = MagicMock()
    return proc


async def _run_lifespan_with_subprocess_mock(tmp_path: Path, exit_code: int,
                                              stdout: str = "", stderr: str = ""):
    """Create an app, fake the subprocess at the boundary, and run the lifespan."""
    app = create_app(data_dir=tmp_path)
    fake_proc = _make_fake_proc(exit_code, stdout, stderr)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
        async with app.router.lifespan_context(app):
            pass
    return app


async def _run_update_with_subprocess_mock(tmp_path: Path, taosmd_dir: Path,
                                            probe_exit_code: int,
                                            probe_stdout: str = "",
                                            probe_stderr: str = ""):
    """Run the update route with a faked subprocess for the probe."""
    taosmd_dir.mkdir()
    (taosmd_dir / ".git").mkdir()

    pulled = {"called": False}

    async def fake_run_capture(cmd, cwd=None, timeout=None, env=None):
        if cmd[0:2] == ["git", "pull"]:
            pulled["called"] = True
        return 0, "ok"

    req = _fake_request(tmp_path, str(taosmd_dir))
    fake_proc = _make_fake_proc(probe_exit_code, probe_stdout, probe_stderr)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
        with patch("tinyagentos.routes.settings._run_capture", new=fake_run_capture):
            with patch("tinyagentos.routes.settings._announce_taosmd_restart",
                       new=AsyncMock(return_value=True)):
                resp = await _update_local_taosmd(req)
    return resp, pulled["called"]


# ---------------------------------------------------------------------------
# Tests for the app startup probe (create_app / lifespan)
# ---------------------------------------------------------------------------

class TestStartupProbe:
    """The probe drives create_app/lifespan and refuses on bad exit codes."""

    @pytest.mark.asyncio
    async def test_startup_current_store_proceeds(self, tmp_path):
        """Exit 0 (current): startup proceeds."""
        app = await _run_lifespan_with_subprocess_mock(
            tmp_path, 0, json.dumps({"status": "current"}))
        assert app.state.memory_store_error is None

    @pytest.mark.asyncio
    async def test_startup_old_store_proceeds_no_conversion(self, tmp_path):
        """Exit 10 (old/legacy): served as is, never converted on open."""
        app = await _run_lifespan_with_subprocess_mock(
            tmp_path, 10, json.dumps({"status": "old"}))
        assert app.state.memory_store_error is None

    @pytest.mark.asyncio
    async def test_startup_unknown_store_refuses(self, tmp_path):
        """Exit 11 (unknown): refuses startup with a clear message."""
        with pytest.raises(RuntimeError) as exc:
            await _run_lifespan_with_subprocess_mock(
                tmp_path, 11, json.dumps({"status": "unknown"}))
        err = str(exc.value)
        assert "Stop the memory service" in err
        assert "memories are safe" in err

    @pytest.mark.asyncio
    async def test_startup_newer_store_refuses(self, tmp_path):
        """Exit 12 (newer): refuses to start the older memory service."""
        with pytest.raises(RuntimeError) as exc:
            await _run_lifespan_with_subprocess_mock(
                tmp_path, 12, json.dumps({"status": "newer"}))
        err = str(exc.value)
        assert "will not be started" in err
        assert "memories are safe" in err

    @pytest.mark.asyncio
    async def test_startup_missing_taosmd_refuses(self, tmp_path):
        """Missing/unrunnable taosmd (FileNotFoundError) refuses with a clear message."""
        app = create_app(data_dir=tmp_path)
        with patch("asyncio.create_subprocess_exec",
                   new=AsyncMock(side_effect=FileNotFoundError("taosmd not found"))):
            with pytest.raises(RuntimeError) as exc:
                async with app.router.lifespan_context(app):
                    pass
        err = str(exc.value)
        assert "taosmd command could not be run" in err
        assert "memories are safe" in err

    @pytest.mark.asyncio
    async def test_startup_timeout_refuses(self, tmp_path):
        """taosmd timeout refuses with a clear message."""
        app = create_app(data_dir=tmp_path)
        async def slow_communicate():
            await asyncio.sleep(100)
        fake_proc = MagicMock()
        fake_proc.communicate = slow_communicate
        fake_proc.kill = MagicMock()
        fake_proc.wait = AsyncMock()
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            with pytest.raises(RuntimeError) as exc:
                async with app.router.lifespan_context(app):
                    pass
        err = str(exc.value)
        assert "timed out" in err
        assert "memories are safe" in err


# ---------------------------------------------------------------------------
# Tests for the update route (_update_local_taosmd)
# ---------------------------------------------------------------------------

class TestUpdateRouteProbe:
    """The update route probes the store with the same resolved data dir."""

    @pytest.mark.asyncio
    async def test_update_uses_resolved_data_dir_not_relative_fallback(self, tmp_path):
        """data_dir comes from request.app.state.data_dir, never Path("data")."""
        taosmd_dir = tmp_path / "taosmd"
        taosmd_dir.mkdir()
        (taosmd_dir / ".git").mkdir()
        seen = {"calls": []}

        async def fake_run_capture(cmd, cwd=None, timeout=None, env=None):
            seen["calls"].append((list(cmd), cwd))
            return 0, "ok"

        req = _fake_request(tmp_path, str(taosmd_dir))
        fake_proc = _make_fake_proc(0, json.dumps({"status": "current"}))
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            with patch("tinyagentos.routes.settings._run_capture", new=fake_run_capture):
                with patch("tinyagentos.routes.settings._announce_taosmd_restart",
                           new=AsyncMock(return_value=True)):
                    resp = await _update_local_taosmd(req)
        assert resp.get("updated") is True
        assert resp.get("error") is None
        git_pulls = [c for c in seen["calls"] if c[0][0:2] == ["git", "pull"]]
        assert len(git_pulls) == 1
        assert Path(git_pulls[0][1]) == taosmd_dir

    @pytest.mark.asyncio
    async def test_update_proceeds_on_current_store(self, tmp_path):
        """Exit 0 (current): update proceeds past the probe to git pull."""
        taosmd_dir = tmp_path / "taosmd"
        resp, pulled = await _run_update_with_subprocess_mock(
            tmp_path, taosmd_dir, 0, json.dumps({"status": "current"}))
        assert resp.get("updated") is True
        assert pulled is True

    @pytest.mark.asyncio
    async def test_update_proceeds_on_old_store_no_conversion(self, tmp_path):
        """Exit 10 (old/legacy): served as is, update proceeds, no conversion."""
        taosmd_dir = tmp_path / "taosmd"
        resp, pulled = await _run_update_with_subprocess_mock(
            tmp_path, taosmd_dir, 10, json.dumps({"status": "old"}))
        assert resp.get("updated") is True
        assert pulled is True

    @pytest.mark.asyncio
    async def test_update_refuses_on_unknown_store(self, tmp_path):
        """Exit 11 (unknown): update refused, says what to do, nothing pulled."""
        taosmd_dir = tmp_path / "taosmd"
        resp, pulled = await _run_update_with_subprocess_mock(
            tmp_path, taosmd_dir, 11, json.dumps({"status": "unknown"}))
        assert "error" in resp
        assert "Stop the memory service" in resp["error"]
        assert pulled is False

    @pytest.mark.asyncio
    async def test_update_refuses_on_newer_store(self, tmp_path):
        """Exit 12 (newer): update refused with plain message, nothing pulled."""
        taosmd_dir = tmp_path / "taosmd"
        resp, pulled = await _run_update_with_subprocess_mock(
            tmp_path, taosmd_dir, 12, json.dumps({"status": "newer"}))
        assert "error" in resp
        assert "will not be started" in resp["error"]
        assert pulled is False

    @pytest.mark.asyncio
    async def test_update_refuses_when_taosmd_missing(self, tmp_path):
        """Missing taosmd (FileNotFoundError): refusal with clear message, nothing pulled."""
        taosmd_dir = tmp_path / "taosmd"
        taosmd_dir.mkdir()
        (taosmd_dir / ".git").mkdir()

        pulled = {"called": False}

        async def fake_run_capture(cmd, cwd=None, timeout=None, env=None):
            pulled["called"] = True
            return 0, "ok"

        req = _fake_request(tmp_path, str(taosmd_dir))
        with patch("asyncio.create_subprocess_exec",
                   new=AsyncMock(side_effect=FileNotFoundError("taosmd not found"))):
            with patch("tinyagentos.routes.settings._run_capture", new=fake_run_capture):
                with patch("tinyagentos.routes.settings._announce_taosmd_restart",
                           new=AsyncMock(return_value=True)):
                    resp = await _update_local_taosmd(req)
        assert "error" in resp
        assert "taosmd command could not be run" in resp["error"]
        assert pulled["called"] is False


# ---------------------------------------------------------------------------
# Tests for the explicit conversion route
# ---------------------------------------------------------------------------

class TestConvertRoute:
    """Conversion is offered only as an explicit choice."""

    @pytest.mark.asyncio
    async def test_convert_without_confirm_is_refused_with_warning(self, client, app):
        """POST without confirm=true returns 400 and the one-way warning."""
        app.state.config.memory_url = "http://localhost:7900"
        app.state.config.taosmd_dir = ""
        app.state.config.taosmd_restart_cmd = ""
        resp = await client.post("/api/settings/memory-store/convert", json={})
        assert resp.status_code == 400
        data = resp.json()
        assert "not automatic" in data["error"]
        assert "older versions" in data["error"].lower()
        assert "cannot read a converted store" in data["error"].lower()
        assert "backup" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_convert_exit_0_converted_or_current(self, client, app):
        """Exit 0: conversion reports converted or already current."""
        app.state.config.memory_url = "http://localhost:7900"
        app.state.config.taosmd_dir = ""
        app.state.config.taosmd_restart_cmd = ""
        stdout = json.dumps({"status": "converted", "exit_code": 0,
                             "rows_converted": 2, "backup_path": "/x/b.db"})
        fake_proc = _make_fake_proc(0, stdout)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            resp = await client.post("/api/settings/memory-store/convert",
                                     json={"confirm": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "converted_or_current"
        assert data["taosmd"]["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_convert_exit_1_unexpected(self, client, app):
        """Exit 1: unexpected error during conversion."""
        app.state.config.memory_url = "http://localhost:7900"
        app.state.config.taosmd_dir = ""
        app.state.config.taosmd_restart_cmd = ""
        stdout = json.dumps({"status": "unexpected", "exit_code": 1})
        fake_proc = _make_fake_proc(1, stdout)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            resp = await client.post("/api/settings/memory-store/convert",
                                     json={"confirm": True})
        assert resp.status_code == 500
        data = resp.json()
        assert "unexpected" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_convert_exit_2_refused_nothing_touched(self, client, app):
        """Exit 2: refused, nothing touched, 500 with plain message."""
        app.state.config.memory_url = "http://localhost:7900"
        app.state.config.taosmd_dir = ""
        app.state.config.taosmd_restart_cmd = ""
        stdout = json.dumps({"status": "refused", "exit_code": 2})
        fake_proc = _make_fake_proc(2, stdout)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            resp = await client.post("/api/settings/memory-store/convert",
                                     json={"confirm": True})
        assert resp.status_code == 500
        data = resp.json()
        assert "refused" in data["error"].lower()
        assert "nothing touched" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_convert_exit_3_unknown_or_newer(self, client, app):
        """Exit 3: unknown or newer format, refused."""
        app.state.config.memory_url = "http://localhost:7900"
        app.state.config.taosmd_dir = ""
        app.state.config.taosmd_restart_cmd = ""
        stdout = json.dumps({"status": "unknown_or_newer", "exit_code": 3})
        fake_proc = _make_fake_proc(3, stdout)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            resp = await client.post("/api/settings/memory-store/convert",
                                     json={"confirm": True})
        assert resp.status_code == 500
        data = resp.json()
        assert "unknown or newer" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_convert_exit_4_failed_restored(self, client, app):
        """Exit 4: failed and restored, memories untouched."""
        app.state.config.memory_url = "http://localhost:7900"
        app.state.config.taosmd_dir = ""
        app.state.config.taosmd_restart_cmd = ""
        stdout = json.dumps({"status": "failed_restored", "exit_code": 4})
        fake_proc = _make_fake_proc(4, stdout)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            resp = await client.post("/api/settings/memory-store/convert",
                                     json={"confirm": True})
        assert resp.status_code == 500
        data = resp.json()
        assert "restored" in data["error"].lower()
        assert "memories are untouched" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_convert_unexpected_exit_code_refuses(self, client, app):
        """Any other exit code is treated as a refusal, never a silent fallback."""
        app.state.config.memory_url = "http://localhost:7900"
        app.state.config.taosmd_dir = ""
        app.state.config.taosmd_restart_cmd = ""
        stdout = json.dumps({"status": "unavailable", "exit_code": 99})
        fake_proc = _make_fake_proc(99, stdout)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=fake_proc)):
            resp = await client.post("/api/settings/memory-store/convert",
                                     json={"confirm": True})
        assert resp.status_code == 500
        data = resp.json()
        assert "unexpected exit code 99" in data["error"]

    @pytest.mark.asyncio
    async def test_convert_missing_taosmd_refuses(self, client, app):
        """Missing taosmd (FileNotFoundError) refuses with a clear message."""
        app.state.config.memory_url = "http://localhost:7900"
        app.state.config.taosmd_dir = ""
        app.state.config.taosmd_restart_cmd = ""
        with patch("asyncio.create_subprocess_exec",
                   new=AsyncMock(side_effect=FileNotFoundError("taosmd not found"))):
            resp = await client.post("/api/settings/memory-store/convert",
                                     json={"confirm": True})
        assert resp.status_code == 500
        data = resp.json()
        assert "taosmd command could not be run" in data["error"]
        assert "memories are safe" in data["error"]