"""Memory store guard tests (tsk-55xm2n).

Tests the memory store format guard against the taosmd migrate command-line
contract (the COMMAND LINE, not Python modules):

    taosmd migrate --probe --json --data-dir <dir>
        exit 0 current, 10 old (legacy store served as is, never converted
        on open), 11 unknown (live WAL / corrupt), 12 newer.

    taosmd migrate --convert-vectors --data-dir <dir>
        exit 0 converted or already current, 1 unexpected, 2 refused
        (nothing touched), 3 unknown or newer, 4 failed and restored.

The subprocess is faked at the boundary (_taosmd_probe /
_taosmd_convert_vectors) with fake exit codes; every code is covered. There
are no pass stubs.
"""

import asyncio
import json
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

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


def _probe_result(exit_code: int, state: str | None = None,
                  error: str | None = None,
                  report: dict | None = None) -> dict:
    """Build a fake _taosmd_probe/_taosmd_convert_vectors result for an exit code."""
    return {
        "exit_code": exit_code,
        "state": state,
        "error": error,
        "report": report,
        "stdout": json.dumps(report) if report is not None else "",
        "stderr": "",
    }


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


def _require_app_boundary():
    """The guard must expose the subprocess boundary functions."""
    import tinyagentos.app as app_mod
    assert getattr(app_mod, "_taosmd_probe", None) is not None, (
        "app must expose _taosmd_probe (subprocess taosmd migrate --probe)")
    assert getattr(app_mod, "_taosmd_convert_vectors", None) is not None, (
        "app must expose _taosmd_convert_vectors (subprocess taosmd migrate "
        "--convert-vectors)")


def _require_settings_boundary():
    """The update route must expose the same subprocess boundary functions."""
    assert getattr(settings_mod, "_taosmd_probe", None) is not None, (
        "settings must expose _taosmd_probe")
    assert getattr(settings_mod, "_taosmd_convert_vectors", None) is not None, (
        "settings must expose _taosmd_convert_vectors")


async def _run_lifespan_with_probe(tmp_path: Path, probe_result: dict):
    """Create an app, fake the probe at the boundary, and run the lifespan."""
    _require_app_boundary()
    app = create_app(data_dir=tmp_path)
    with patch("tinyagentos.app._taosmd_probe",
               new=AsyncMock(return_value=probe_result)):
        async with app.router.lifespan_context(app):
            pass
    return app


# ---------------------------------------------------------------------------
# Tests for the app startup probe (create_app / lifespan)
# ---------------------------------------------------------------------------

class TestStartupProbe:
    """The probe drives create_app/lifespan and sets memory_store_error."""

    @pytest.mark.asyncio
    async def test_startup_current_store_no_error(self, tmp_path):
        """Exit 0 (current): startup proceeds, no error surfaced."""
        app = await _run_lifespan_with_probe(
            tmp_path, _probe_result(0, "current"))
        assert app.state.memory_store_error is None

    @pytest.mark.asyncio
    async def test_startup_old_store_no_error_no_conversion(self, tmp_path):
        """Exit 10 (old/legacy): served as is, never converted on open, no error."""
        app = await _run_lifespan_with_probe(
            tmp_path, _probe_result(10, "old"))
        assert app.state.memory_store_error is None

    @pytest.mark.asyncio
    async def test_startup_unknown_store_refuses_what_to_do(self, tmp_path):
        """Exit 11 (unknown): refuses and says what to do."""
        app = await _run_lifespan_with_probe(
            tmp_path,
            _probe_result(11, "unknown",
                          "The memory store format could not be determined "
                          "(unreadable store, a live writer, or an "
                          "uncheckpointed WAL). The store is unchanged and "
                          "your memories are safe. Stop the memory service "
                          "and retry."))
        err = app.state.memory_store_error
        assert err is not None
        assert "Stop the memory service" in err
        assert "memories are safe" in err

    @pytest.mark.asyncio
    async def test_startup_newer_store_refuses_plain_message(self, tmp_path):
        """Exit 12 (newer): refuses to start the older memory service."""
        app = await _run_lifespan_with_probe(
            tmp_path,
            _probe_result(12, "newer",
                          "The memory store on disk was written by a newer "
                          "version of the memory service. The older memory "
                          "service will not be started on this store. The "
                          "store is unchanged and your memories are safe. "
                          "Please update taOS to a version that supports "
                          "this store format."))
        err = app.state.memory_store_error
        assert err is not None
        assert "will not be started" in err
        assert "memories are safe" in err

    @pytest.mark.asyncio
    async def test_startup_missing_taosmd_refuses_clear_message(self, tmp_path):
        """Missing/unrunnable taosmd is a refusal with a clear message."""
        app = await _run_lifespan_with_probe(
            tmp_path,
            _probe_result(-1, "unavailable",
                          "The taosmd command could not be run. The store is "
                          "untouched and your memories are safe."))
        err = app.state.memory_store_error
        assert err is not None
        assert "taosmd command could not be run" in err
        assert "memories are safe" in err


# ---------------------------------------------------------------------------
# Tests for the update route (_update_local_taosmd)
# ---------------------------------------------------------------------------

class TestUpdateRouteProbe:
    """The update route probes the store with the same resolved data dir."""

    @pytest.mark.asyncio
    async def test_update_uses_resolved_data_dir_not_relative_fallback(self, tmp_path):
        """data_dir comes from request.app.state.data_dir, never Path("data")."""
        _require_settings_boundary()
        taosmd_dir = tmp_path / "taosmd"
        taosmd_dir.mkdir()
        (taosmd_dir / ".git").mkdir()

        seen = {"calls": []}

        async def fake_run_capture(cmd, cwd=None, timeout=None, env=None):
            seen["calls"].append((list(cmd), cwd))
            return 0, "ok"

        req = _fake_request(tmp_path, str(taosmd_dir))
        with patch("tinyagentos.routes.settings._taosmd_probe",
                   new=AsyncMock(return_value=_probe_result(0, "current"))):
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
        _require_settings_boundary()
        taosmd_dir = tmp_path / "taosmd"
        taosmd_dir.mkdir()
        (taosmd_dir / ".git").mkdir()
        pulled = {"called": False}

        async def fake_run_capture(cmd, cwd=None, timeout=None, env=None):
            if cmd[0:2] == ["git", "pull"]:
                pulled["called"] = True
            return 0, "ok"

        req = _fake_request(tmp_path, str(taosmd_dir))
        with patch("tinyagentos.routes.settings._taosmd_probe",
                   new=AsyncMock(return_value=_probe_result(0, "current"))):
            with patch("tinyagentos.routes.settings._run_capture", new=fake_run_capture):
                with patch("tinyagentos.routes.settings._announce_taosmd_restart",
                           new=AsyncMock(return_value=True)):
                    resp = await _update_local_taosmd(req)
        assert resp.get("updated") is True
        assert pulled["called"] is True

    @pytest.mark.asyncio
    async def test_update_proceeds_on_old_store_no_conversion(self, tmp_path):
        """Exit 10 (old/legacy): served as is, update proceeds, no conversion."""
        _require_settings_boundary()
        taosmd_dir = tmp_path / "taosmd"
        taosmd_dir.mkdir()
        (taosmd_dir / ".git").mkdir()
        pulled = {"called": False}

        async def fake_run_capture(cmd, cwd=None, timeout=None, env=None):
            if cmd[0:2] == ["git", "pull"]:
                pulled["called"] = True
            return 0, "ok"

        req = _fake_request(tmp_path, str(taosmd_dir))
        with patch("tinyagentos.routes.settings._taosmd_probe",
                   new=AsyncMock(return_value=_probe_result(10, "old"))):
            with patch("tinyagentos.routes.settings._run_capture", new=fake_run_capture):
                with patch("tinyagentos.routes.settings._announce_taosmd_restart",
                           new=AsyncMock(return_value=True)):
                    resp = await _update_local_taosmd(req)
        assert resp.get("updated") is True
        assert pulled["called"] is True

    @pytest.mark.asyncio
    async def test_update_refuses_on_unknown_store(self, tmp_path):
        """Exit 11 (unknown): update refused, says what to do, nothing pulled."""
        _require_settings_boundary()
        taosmd_dir = tmp_path / "taosmd"
        taosmd_dir.mkdir()
        (taosmd_dir / ".git").mkdir()
        pulled = {"called": False}

        async def fake_run_capture(cmd, cwd=None, timeout=None, env=None):
            pulled["called"] = True
            return 0, "ok"

        req = _fake_request(tmp_path, str(taosmd_dir))
        with patch("tinyagentos.routes.settings._taosmd_probe",
                   new=AsyncMock(return_value=_probe_result(
                       11, "unknown",
                       "The memory store format could not be determined "
                       "(unreadable store, a live writer, or an "
                       "uncheckpointed WAL). Stop the memory service and "
                       "retry."))):
            with patch("tinyagentos.routes.settings._run_capture", new=fake_run_capture):
                with patch("tinyagentos.routes.settings._announce_taosmd_restart",
                           new=AsyncMock(return_value=True)):
                    resp = await _update_local_taosmd(req)
        assert "error" in resp
        assert "Stop the memory service" in resp["error"]
        assert pulled["called"] is False

    @pytest.mark.asyncio
    async def test_update_refuses_on_newer_store(self, tmp_path):
        """Exit 12 (newer): update refused with plain message, nothing pulled."""
        _require_settings_boundary()
        taosmd_dir = tmp_path / "taosmd"
        taosmd_dir.mkdir()
        (taosmd_dir / ".git").mkdir()
        pulled = {"called": False}

        async def fake_run_capture(cmd, cwd=None, timeout=None, env=None):
            pulled["called"] = True
            return 0, "ok"

        req = _fake_request(tmp_path, str(taosmd_dir))
        with patch("tinyagentos.routes.settings._taosmd_probe",
                   new=AsyncMock(return_value=_probe_result(
                       12, "newer",
                       "The memory store on disk was written by a newer "
                       "version of the memory service. The older memory "
                       "service will not be started on this store."))):
            with patch("tinyagentos.routes.settings._run_capture", new=fake_run_capture):
                with patch("tinyagentos.routes.settings._announce_taosmd_restart",
                           new=AsyncMock(return_value=True)):
                    resp = await _update_local_taosmd(req)
        assert "error" in resp
        assert "will not be started" in resp["error"]
        assert pulled["called"] is False

    @pytest.mark.asyncio
    async def test_update_refuses_when_taosmd_missing(self, tmp_path):
        """Missing taosmd (exit -1): refusal with clear message, nothing pulled."""
        _require_settings_boundary()
        taosmd_dir = tmp_path / "taosmd"
        taosmd_dir.mkdir()
        (taosmd_dir / ".git").mkdir()
        pulled = {"called": False}

        async def fake_run_capture(cmd, cwd=None, timeout=None, env=None):
            pulled["called"] = True
            return 0, "ok"

        req = _fake_request(tmp_path, str(taosmd_dir))
        with patch("tinyagentos.routes.settings._taosmd_probe",
                   new=AsyncMock(return_value=_probe_result(
                       -1, "unavailable",
                       "The taosmd command could not be run. The store is "
                       "untouched and your memories are safe."))):
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
        _require_settings_boundary()
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
        _require_settings_boundary()
        app.state.config.memory_url = "http://localhost:7900"
        app.state.config.taosmd_dir = ""
        app.state.config.taosmd_restart_cmd = ""
        with patch("tinyagentos.routes.settings._taosmd_convert_vectors",
                   new=AsyncMock(return_value=_probe_result(
                       0, "converted_or_current",
                       report={"status": "converted", "exit_code": 0,
                               "rows_converted": 2,
                               "backup_path": "/x/b.db"}))):
            resp = await client.post("/api/settings/memory-store/convert",
                                     json={"confirm": True})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "converted_or_current"
        assert data["taosmd"]["exit_code"] == 0

    @pytest.mark.asyncio
    async def test_convert_exit_2_refused_nothing_touched(self, client, app):
        """Exit 2: refused, nothing touched, 500 with plain message."""
        _require_settings_boundary()
        app.state.config.memory_url = "http://localhost:7900"
        app.state.config.taosmd_dir = ""
        app.state.config.taosmd_restart_cmd = ""
        with patch("tinyagentos.routes.settings._taosmd_convert_vectors",
                   new=AsyncMock(return_value=_probe_result(
                       2, "refused",
                       error="The memory store conversion was refused (nothing "
                             "touched). A running server may hold the store."))):
            resp = await client.post("/api/settings/memory-store/convert",
                                     json={"confirm": True})
        assert resp.status_code == 500
        data = resp.json()
        assert "refused" in data["error"].lower()
        assert "nothing touched" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_convert_exit_3_unknown_or_newer(self, client, app):
        """Exit 3: unknown or newer format, refused."""
        _require_settings_boundary()
        app.state.config.memory_url = "http://localhost:7900"
        app.state.config.taosmd_dir = ""
        app.state.config.taosmd_restart_cmd = ""
        with patch("tinyagentos.routes.settings._taosmd_convert_vectors",
                   new=AsyncMock(return_value=_probe_result(
                       3, "unknown_or_newer",
                       error="The memory store conversion was refused: the store "
                             "format is unknown or newer than this build."))):
            resp = await client.post("/api/settings/memory-store/convert",
                                     json={"confirm": True})
        assert resp.status_code == 500
        data = resp.json()
        assert "unknown or newer" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_convert_exit_4_failed_restored(self, client, app):
        """Exit 4: failed and restored, memories untouched."""
        _require_settings_boundary()
        app.state.config.memory_url = "http://localhost:7900"
        app.state.config.taosmd_dir = ""
        app.state.config.taosmd_restart_cmd = ""
        with patch("tinyagentos.routes.settings._taosmd_convert_vectors",
                   new=AsyncMock(return_value=_probe_result(
                       4, "failed_restored",
                       error="The memory store conversion failed and the original "
                             "store was restored. Your memories are untouched."))):
            resp = await client.post("/api/settings/memory-store/convert",
                                     json={"confirm": True})
        assert resp.status_code == 500
        data = resp.json()
        assert "restored" in data["error"].lower()
        assert "memories are untouched" in data["error"].lower()

    @pytest.mark.asyncio
    async def test_convert_unexpected_exit_code_refuses(self, client, app):
        """Any other exit code is treated as a refusal, never a silent fallback."""
        _require_settings_boundary()
        app.state.config.memory_url = "http://localhost:7900"
        app.state.config.taosmd_dir = ""
        app.state.config.taosmd_restart_cmd = ""
        with patch("tinyagentos.routes.settings._taosmd_convert_vectors",
                   new=AsyncMock(return_value=_probe_result(
                       99, "unavailable",
                       error="The taosmd store conversion returned an unexpected "
                             "exit code 99."))):
            resp = await client.post("/api/settings/memory-store/convert",
                                     json={"confirm": True})
        assert resp.status_code == 500
        data = resp.json()
        assert "unexpected exit code 99" in data["error"]
