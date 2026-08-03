"""Unit tests for tinyagentos.restart_orchestrator.

Tests cover pure logic and public functions/methods that can run in-process.
External services (network, containers, GPU, LLM) are mocked.
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import tinyagentos.restart_orchestrator as ro


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _app_state(tmp_path, *, agents=None, data_dir=None):
    """Minimal app_state namespace for tests."""
    if agents is None:
        agents = []
    if data_dir is None:
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
    notifications = SimpleNamespace(add=AsyncMock())
    config = SimpleNamespace(
        agents=agents,
        config_path=tmp_path / "config.yaml",
    )
    return SimpleNamespace(
        config=config,
        data_dir=data_dir,
        notifications=notifications,
        _background_tasks=set(),
    )


@pytest.fixture(autouse=True)
def _no_config_write(monkeypatch):
    """Prevent real config writes during tests."""
    import tinyagentos.config as cfg

    monkeypatch.setattr(cfg, "save_config_locked", AsyncMock())


# ---------------------------------------------------------------------------
# _pending_restart_path
# ---------------------------------------------------------------------------

class TestPendingRestartPath:
    def test_uses_env_var(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        p = ro._pending_restart_path()
        assert p == tmp_path / "pending-restart.json"

    def test_uses_install_data_dir_when_available(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TAOS_DATA_DIR", raising=False)
        install_root = tmp_path / "tinyagentos"
        install_root.mkdir()
        data_dir = install_root / "data"
        data_dir.mkdir()
        (tmp_path / "data").mkdir()
        monkeypatch.setattr(ro, "__file__", str(install_root / "restart_orchestrator.py"))
        p = ro._pending_restart_path()
        assert p == tmp_path / "data" / "pending-restart.json"

    def test_falls_back_to_home(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TAOS_DATA_DIR", raising=False)
        home = tmp_path / "home"
        home.mkdir()
        fake_module = tmp_path / "tinyagentos" / "restart_orchestrator.py"
        fake_module.parent.mkdir(parents=True)
        monkeypatch.setattr(ro, "__file__", str(fake_module))
        monkeypatch.setenv("HOME", str(home))
        p = ro._pending_restart_path()
        assert p == home / ".config" / "taos" / "pending-restart.json"


# ---------------------------------------------------------------------------
# write_pending_restart / read_pending_restart / clear_pending_restart
# ---------------------------------------------------------------------------

class TestPendingRestartFlag:
    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        ro.write_pending_restart("abc123")
        result = ro.read_pending_restart()
        assert result is not None
        assert result["target_sha"] == "abc123"
        assert "pulled_at" in result

    def test_read_missing_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        assert ro.read_pending_restart() is None

    def test_clear_missing_is_safe(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        ro.clear_pending_restart()

    def test_clear_removes_existing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        ro.write_pending_restart("sha")
        ro.clear_pending_restart()
        assert ro.read_pending_restart() is None

    def test_read_invalid_json_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        flag = tmp_path / "pending-restart.json"
        flag.write_text("not json")
        assert ro.read_pending_restart() is None


# ---------------------------------------------------------------------------
# _load_or_synthesize_note
# ---------------------------------------------------------------------------

class TestLoadOrSynthesizeNote:
    def test_reads_valid_note(self, tmp_path):
        note = {"reason": "pause", "next_step_hint": "carry on"}
        p = tmp_path / "resume_note.json"
        p.write_text(json.dumps(note))
        assert ro._load_or_synthesize_note(p) == note

    def test_synthesizes_when_missing(self, tmp_path):
        p = tmp_path / "missing.json"
        result = ro._load_or_synthesize_note(p)
        assert result["reason"] == "restart"
        assert result["next_step_hint"] == "controller restarted; resume normal operation"

    def test_synthesizes_when_not_dict(self, tmp_path):
        p = tmp_path / "resume_note.json"
        p.write_text(json.dumps("bad"))
        result = ro._load_or_synthesize_note(p)
        assert result["reason"] == "restart"

    def test_synthesizes_when_invalid_json(self, tmp_path):
        p = tmp_path / "resume_note.json"
        p.write_text("not json")
        result = ro._load_or_synthesize_note(p)
        assert result["reason"] == "restart"


# ---------------------------------------------------------------------------
# _post_resume
# ---------------------------------------------------------------------------

class TestPostResume:
    @pytest.mark.asyncio
    async def test_returns_true_on_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        async def fake_post(self, url, json):
            return mock_resp

        with patch("httpx.AsyncClient.post", new=fake_post):
            result = await ro._post_resume("10.0.0.1", 8080, {"reason": "restart"})
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_non_200(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 500

        async def fake_post(self, url, json):
            return mock_resp

        with patch("httpx.AsyncClient.post", new=fake_post):
            result = await ro._post_resume("10.0.0.1", 8080, {"reason": "restart"})
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_network_error(self):
        async def fake_post(self, url, json):
            raise ConnectionError("refused")

        with patch("httpx.AsyncClient.post", new=fake_post):
            result = await ro._post_resume("10.0.0.1", 8080, {"reason": "restart"})
        assert result is False


# ---------------------------------------------------------------------------
# RestartOrchestrator
# ---------------------------------------------------------------------------

class TestRestartOrchestrator:
    def test_get_status_returns_copy(self, tmp_path):
        state = _app_state(tmp_path)
        orch = ro.RestartOrchestrator(state)
        status = orch.get_status()
        status["phase"] = "mutated"
        assert orch._status["phase"] == "idle"

    @pytest.mark.asyncio
    async def test_prepare_empty_agents_returns_empty_report(self, tmp_path):
        state = _app_state(tmp_path, agents=[])
        orch = ro.RestartOrchestrator(state)
        report = await orch.prepare("all", "stop")
        assert report == {}
        assert orch.get_status()["phase"] == "ready"

    @pytest.mark.asyncio
    async def test_prepare_scope_all(self, tmp_path, monkeypatch):
        agents = [
            {"name": "a1", "host": "10.0.0.1", "port": 8080},
            {"name": "a2", "host": "10.0.0.2", "port": 8080},
        ]
        state = _app_state(tmp_path, agents=agents)
        orch = ro.RestartOrchestrator(state)

        async def fake_prepare(agent, reason, data_dir):
            return {"status": "ready", "duration_s": 0.1, "note_path": None}

        monkeypatch.setattr(orch, "_prepare_agent", fake_prepare)
        report = await orch.prepare("all", "stop")
        assert "a1" in report
        assert "a2" in report

    @pytest.mark.asyncio
    async def test_prepare_scope_list_filters_agents(self, tmp_path, monkeypatch):
        agents = [
            {"name": "a1", "host": "10.0.0.1", "port": 8080},
            {"name": "a2", "host": "10.0.0.2", "port": 8080},
        ]
        state = _app_state(tmp_path, agents=agents)
        orch = ro.RestartOrchestrator(state)

        async def fake_prepare(agent, reason, data_dir):
            return {"status": "ready", "duration_s": 0.1, "note_path": None}

        monkeypatch.setattr(orch, "_prepare_agent", fake_prepare)
        report = await orch.prepare(["a1"], "pause")
        assert "a1" in report
        assert "a2" not in report

    @pytest.mark.asyncio
    async def test_prepare_timeout_sets_status(self, tmp_path):
        agent = {"name": "slow", "host": "10.0.0.1", "port": 8080}
        state = _app_state(tmp_path, agents=[agent])
        orch = ro.RestartOrchestrator(state)

        async def timeout_prepare(*args, **kwargs):
            raise asyncio.TimeoutError()

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(orch, "_prepare_agent", timeout_prepare)
        try:
            report = await orch.prepare("all", "stop")
        finally:
            monkeypatch.undo()

        assert report["slow"]["status"] == "timeout"
        assert report["slow"]["duration_s"] == 300
        assert orch.get_status()["agents"]["slow"]["status"] == "timeout"

    @pytest.mark.asyncio
    async def test_prepare_exception_sets_status(self, tmp_path):
        agent = {"name": "bad", "host": "10.0.0.1", "port": 8080}
        state = _app_state(tmp_path, agents=[agent])
        orch = ro.RestartOrchestrator(state)

        async def error_prepare(*args, **kwargs):
            raise ValueError("boom")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(orch, "_prepare_agent", error_prepare)
        try:
            report = await orch.prepare("all", "stop")
        finally:
            monkeypatch.undo()

        assert report["bad"]["status"] == "error"
        assert orch.get_status()["agents"]["bad"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_prepare_success_updates_status(self, tmp_path, monkeypatch):
        agent = {"name": "good", "host": "10.0.0.1", "port": 8080}
        state = _app_state(tmp_path, agents=[agent])
        orch = ro.RestartOrchestrator(state)

        async def ok_prepare(agent, reason, data_dir):
            return {"status": "ready", "duration_s": 0.5, "note_path": "/n"}

        monkeypatch.setattr(orch, "_prepare_agent", ok_prepare)
        report = await orch.prepare("all", "stop")
        assert report["good"]["status"] == "ready"
        assert orch.get_status()["phase"] == "ready"


# ---------------------------------------------------------------------------
# _prepare_agent
# ---------------------------------------------------------------------------

class TestPrepareAgent:
    @pytest.mark.asyncio
    async def test_no_host_writes_controller_note(self, tmp_path, monkeypatch):
        agent = {"name": "nohost", "host": "", "port": 8080}
        state = _app_state(tmp_path, agents=[agent])
        orch = ro.RestartOrchestrator(state)

        written = {}

        async def fake_write(a, r, d):
            written["note"] = (a["name"], r)
            return str(d / "note.json")

        monkeypatch.setattr(orch, "_write_controller_note", fake_write)
        result = await orch._prepare_agent(agent, "stop", state.data_dir)

        assert result["status"] == "ready"
        assert written["note"] == ("nohost", "stop")
        assert agent["paused"] is True
        assert result["note_path"] == str(state.data_dir / "note.json")

    @pytest.mark.asyncio
    async def test_host_200_uses_note_path_from_response(self, tmp_path, monkeypatch):
        agent = {"name": "remote", "host": "10.0.0.1", "port": 8080}
        state = _app_state(tmp_path, agents=[agent])
        orch = ro.RestartOrchestrator(state)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"note_path": "/remote/note.json"}

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        monkeypatch.setattr(orch, "_write_controller_note", AsyncMock())
        with patch("httpx.AsyncClient", return_value=mock_cm):
            result = await orch._prepare_agent(agent, "stop", state.data_dir)

        assert result["note_path"] == "/remote/note.json"
        assert result["status"] == "ready"
        assert agent["paused"] is True

    @pytest.mark.asyncio
    async def test_host_non_200_writes_controller_note(self, tmp_path, monkeypatch):
        agent = {"name": "remote", "host": "10.0.0.1", "port": 8080}
        state = _app_state(tmp_path, agents=[agent])
        orch = ro.RestartOrchestrator(state)

        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        written = {}

        async def fake_write(a, r, d):
            written["note"] = (a["name"], r)
            return str(d / "note.json")

        monkeypatch.setattr(orch, "_write_controller_note", fake_write)
        with patch("httpx.AsyncClient", return_value=mock_cm):
            result = await orch._prepare_agent(agent, "stop", state.data_dir)

        assert result["note_path"] == str(state.data_dir / "note.json")
        assert agent["paused"] is True

    @pytest.mark.asyncio
    async def test_host_exception_writes_controller_note(self, tmp_path, monkeypatch):
        agent = {"name": "remote", "host": "10.0.0.1", "port": 8080}
        state = _app_state(tmp_path, agents=[agent])
        orch = ro.RestartOrchestrator(state)

        mock_client = MagicMock()
        mock_client.post = AsyncMock(side_effect=ConnectionError("refused"))
        mock_cm = MagicMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_client)
        mock_cm.__aexit__ = AsyncMock(return_value=False)

        written = {}

        async def fake_write(a, r, d):
            written["note"] = (a["name"], r)
            return str(d / "note.json")

        monkeypatch.setattr(orch, "_write_controller_note", fake_write)
        with patch("httpx.AsyncClient", return_value=mock_cm):
            result = await orch._prepare_agent(agent, "stop", state.data_dir)

        assert result["note_path"] == str(state.data_dir / "note.json")
        assert agent["paused"] is True


# ---------------------------------------------------------------------------
# _write_controller_note
# ---------------------------------------------------------------------------

class TestWriteControllerNote:
    @pytest.mark.asyncio
    async def test_writes_note_file(self, tmp_path):
        agent = {"name": "agent1"}
        orch = ro.RestartOrchestrator(_app_state(tmp_path))
        result = await orch._write_controller_note(agent, "stop", tmp_path)
        p = Path(result)
        assert p.exists()
        data = json.loads(p.read_text())
        assert data["reason"] == "stop"
        assert data["paused_at"] == int(time.time())
        assert data["next_step_hint"] == (
            "controller-side fallback — agent framework did not implement /prepare-for-shutdown"
        )
        assert data["context_snapshot"] == {}


# ---------------------------------------------------------------------------
# apply_pending_restart_check
# ---------------------------------------------------------------------------

class TestApplyPendingRestartCheck:
    @pytest.mark.asyncio
    async def test_no_pending_returns_early(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ro, "read_pending_restart", lambda: None)
        state = _app_state(tmp_path)
        await ro.apply_pending_restart_check(state)
        state.notifications.add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_matching_sha_clears_flag(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ro, "read_pending_restart", lambda: {"target_sha": "abc123"})
        monkeypatch.setattr(ro, "clear_pending_restart", MagicMock())

        mock_stdout = MagicMock()
        mock_stdout.decode.return_value = "abc123\n"
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(mock_stdout, b""))

        async def fake_create(*args, **kwargs):
            return mock_proc

        state = _app_state(tmp_path)
        with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
            await ro.apply_pending_restart_check(state)

        titles = [c.kwargs["title"] for c in state.notifications.add.await_args_list]
        assert any("Update applied" in t for t in titles)
        ro.clear_pending_restart.assert_called_once()

    @pytest.mark.asyncio
    async def test_mismatched_sha_posts_warning(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ro, "read_pending_restart", lambda: {"target_sha": "abc123"})
        monkeypatch.setattr(ro, "clear_pending_restart", MagicMock())

        mock_stdout = MagicMock()
        mock_stdout.decode.return_value = "def456\n"
        mock_proc = MagicMock()
        mock_proc.communicate = AsyncMock(return_value=(mock_stdout, b""))

        async def fake_create(*args, **kwargs):
            return mock_proc

        state = _app_state(tmp_path)
        with patch("asyncio.create_subprocess_exec", side_effect=fake_create):
            await ro.apply_pending_restart_check(state)

        titles = [c.kwargs["title"] for c in state.notifications.add.await_args_list]
        assert any("Restart happened but code didn't update" in t for t in titles)
        ro.clear_pending_restart.assert_not_called()

    @pytest.mark.asyncio
    async def test_subprocess_failure_treated_as_mismatch(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ro, "read_pending_restart", lambda: {"target_sha": "abc123"})
        monkeypatch.setattr(ro, "clear_pending_restart", MagicMock())

        async def failing_create(*args, **kwargs):
            raise OSError("git failed")

        state = _app_state(tmp_path)
        with patch("asyncio.create_subprocess_exec", side_effect=failing_create):
            await ro.apply_pending_restart_check(state)

        titles = [c.kwargs["title"] for c in state.notifications.add.await_args_list]
        assert any("Restart happened but code didn't update" in t for t in titles)
        ro.clear_pending_restart.assert_not_called()


# ---------------------------------------------------------------------------
# _unpause
# ---------------------------------------------------------------------------

class TestUnpause:
    @pytest.mark.asyncio
    async def test_success_unpauses_and_deletes_note(self, tmp_path, monkeypatch):
        agent = {"name": "test", "paused": True}
        state = _app_state(tmp_path, agents=[agent])
        note_path = tmp_path / "note.json"
        note_path.write_text("{}")

        saved = []

        async def fake_save(cfg, path):
            saved.append((cfg, path))

        monkeypatch.setattr("tinyagentos.config.save_config_locked", fake_save)
        await ro._unpause(state, agent, note_path)

        assert agent["paused"] is False
        assert not note_path.exists()
        assert len(saved) == 1

    @pytest.mark.asyncio
    async def test_save_failure_keeps_note_and_unpauses(self, tmp_path, monkeypatch):
        agent = {"name": "test", "paused": True}
        state = _app_state(tmp_path, agents=[agent])
        note_path = tmp_path / "note.json"
        note_path.write_text("{}")

        async def fake_save(cfg, path):
            raise OSError("disk full")

        monkeypatch.setattr("tinyagentos.config.save_config_locked", fake_save)
        await ro._unpause(state, agent, note_path)

        assert agent["paused"] is False
        assert note_path.exists()

    @pytest.mark.asyncio
    async def test_note_none_still_saves(self, tmp_path, monkeypatch):
        agent = {"name": "test", "paused": True}
        state = _app_state(tmp_path, agents=[agent])

        saved = []

        async def fake_save(cfg, path):
            saved.append((cfg, path))

        monkeypatch.setattr("tinyagentos.config.save_config_locked", fake_save)
        await ro._unpause(state, agent, None)

        assert agent["paused"] is False
        assert len(saved) == 1


# ---------------------------------------------------------------------------
# resume_agents_from_notes -- additional edge cases not in existing file
# ---------------------------------------------------------------------------

class TestResumeAgentsFromNotes:
    @pytest.mark.asyncio
    async def test_no_paused_agents_is_noop(self, tmp_path):
        agent = {"name": "up", "host": "10.0.0.1", "paused": False}
        state = _app_state(tmp_path, agents=[agent])

        await ro.resume_agents_from_notes(state)

        state.notifications.add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_hostless_agent_unpaused_without_resume_call(self, tmp_path, monkeypatch):
        agent = {"name": "hostless", "host": "", "paused": True}
        state = _app_state(tmp_path, agents=[agent])

        await ro.resume_agents_from_notes(state)

        assert agent["paused"] is False
        state.notifications.add.assert_awaited()

    @pytest.mark.asyncio
    async def test_synthesizes_note_when_missing(self, tmp_path, monkeypatch):
        agent = {"name": "synth", "host": "10.0.0.1", "port": 8080, "paused": True}
        state = _app_state(tmp_path, agents=[agent])

        posted = {}

        async def fake_post(host, port, note):
            posted["note"] = note
            return True

        monkeypatch.setattr(ro, "_post_resume", fake_post)
        await ro.resume_agents_from_notes(state)

        assert posted["note"]["reason"] == "restart"
        assert agent["paused"] is False
