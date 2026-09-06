"""Tests for the worker self-update orchestrator (taOS #890 C3)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.worker.self_update import (
    clear_update_marker,
    read_update_marker,
    _write_update_marker,
    _detect_package_manager,
)


# ── Marker file helpers ───────────────────────────────────────────────


class TestUpdateMarker:
    """Tests for the in-progress update marker (filesystem I/O)."""

    def test_write_and_read_marker(self, tmp_path: Path):
        state_dir = tmp_path / "worker-state"
        _write_update_marker(
            state_dir,
            checkpoint_tag="taos-worker-pre-update-20260717-120000",
            from_sha="abc1234",
            to_sha="def5678",
        )

        marker = read_update_marker(state_dir)
        assert marker is not None
        assert marker["checkpoint_tag"] == "taos-worker-pre-update-20260717-120000"
        assert marker["from_sha"] == "abc1234"
        assert marker["to_sha"] == "def5678"

    def test_read_marker_nonexistent(self, tmp_path: Path):
        state_dir = tmp_path / "nonexistent"
        assert read_update_marker(state_dir) is None

    def test_clear_marker(self, tmp_path: Path):
        state_dir = tmp_path / "worker-state"
        _write_update_marker(state_dir, "tag", "a", "b")
        assert read_update_marker(state_dir) is not None

        clear_update_marker(state_dir)
        assert read_update_marker(state_dir) is None

    def test_clear_marker_idempotent(self, tmp_path: Path):
        """Clearing a non-existent marker should not raise."""
        state_dir = tmp_path / "no-marker"
        clear_update_marker(state_dir)  # should not raise

    def test_read_marker_invalid_json(self, tmp_path: Path):
        state_dir = tmp_path / "worker-state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "update-in-progress.json").write_text("not json")

        assert read_update_marker(state_dir) is None

    def test_write_marker_creates_directory(self, tmp_path: Path):
        state_dir = tmp_path / "deeply" / "nested" / "state"
        _write_update_marker(state_dir, "tag", "a", "b")
        assert read_update_marker(state_dir) is not None


# ── Package manager detection ────────────────────────────────────────


class TestDetectPackageManager:
    """Tests for _detect_package_manager()."""

    def test_pip_default(self, monkeypatch, tmp_path: Path):
        """When no uv.lock exists, should return 'pip'."""
        # Override _repo_dir and _install_dir to use tmp_path
        import tinyagentos.worker.self_update as su

        monkeypatch.setattr(su, "_install_dir", lambda: tmp_path)
        monkeypatch.setattr(su, "_repo_dir", lambda: tmp_path)
        # No uv.lock in tmp_path — should detect pip
        assert su._detect_package_manager() == "pip"

    def test_uv_detected_when_lockfile_present(self, monkeypatch, tmp_path: Path):
        """When uv.lock exists, should return 'uv'."""
        import tinyagentos.worker.self_update as su

        (tmp_path / "uv.lock").write_text("")
        monkeypatch.setattr(su, "_install_dir", lambda: tmp_path)
        monkeypatch.setattr(su, "_repo_dir", lambda: tmp_path)
        assert su._detect_package_manager() == "uv"


# ── create_checkpoint ─────────────────────────────────────────────────


class TestCreateCheckpoint:
    @pytest.mark.asyncio
    async def test_checkpoint_success(self, monkeypatch, tmp_path: Path):
        """create_checkpoint should return tag and SHA on success."""
        import tinyagentos.worker.self_update as su

        # Mock _run_helper to return success
        async def mock_run_helper(args, timeout=600):
            return {"ok": True, "output": "taos-worker-pre-update-20260717-120000", "exit_code": 0}

        # Mock _run_git to return a known SHA
        async def mock_run_git(args, cwd=None, timeout=120):
            return 0, "abc1234def5678\n"

        monkeypatch.setattr(su, "_run_helper", mock_run_helper)
        monkeypatch.setattr(su, "_run_git", mock_run_git)

        result = await su.create_checkpoint()
        assert result["ok"] is True
        assert result["checkpoint_tag"] == "taos-worker-pre-update-20260717-120000"
        assert result["git_sha"] == "abc1234def5678"

    @pytest.mark.asyncio
    async def test_checkpoint_helper_fails(self, monkeypatch, tmp_path: Path):
        """create_checkpoint should return ok=False when helper fails."""
        import tinyagentos.worker.self_update as su

        async def mock_run_helper(args, timeout=600):
            return {"ok": False, "output": "helper not found", "exit_code": -1}

        async def mock_run_git(args, cwd=None, timeout=120):
            return 0, "sha1234\n"

        monkeypatch.setattr(su, "_run_helper", mock_run_helper)
        monkeypatch.setattr(su, "_run_git", mock_run_git)

        result = await su.create_checkpoint()
        assert result["ok"] is False
        assert result["checkpoint_tag"] == ""


# ── pull_update ───────────────────────────────────────────────────────


class TestPullUpdate:
    @pytest.mark.asyncio
    async def test_pull_success(self, monkeypatch, tmp_path: Path):
        """pull_update should fetch and checkout successfully."""
        import tinyagentos.worker.self_update as su

        monkeypatch.setattr(su, "_repo_dir", lambda: tmp_path)

        async def mock_run_git(args, cwd=None, timeout=120):
            if args[0] == "fetch":
                return 0, ""
            if args[0] == "checkout":
                return 0, ""
            if args[0] == "rev-parse":
                return 0, "def5678\n"
            return 1, "unknown"

        monkeypatch.setattr(su, "_run_git", mock_run_git)

        result = await su.pull_update("origin/master")
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_pull_fetch_fails(self, monkeypatch, tmp_path: Path):
        """pull_update should fail when git fetch fails."""
        import tinyagentos.worker.self_update as su

        monkeypatch.setattr(su, "_repo_dir", lambda: tmp_path)

        async def mock_run_git(args, cwd=None, timeout=120):
            if args[0] == "fetch":
                return 128, "fatal: could not fetch"
            return 1, "unknown"

        monkeypatch.setattr(su, "_run_git", mock_run_git)

        result = await su.pull_update("origin/master")
        assert result["ok"] is False

    @pytest.mark.asyncio
    async def test_pull_checkout_fails(self, monkeypatch, tmp_path: Path):
        """pull_update should fail when git checkout fails."""
        import tinyagentos.worker.self_update as su

        monkeypatch.setattr(su, "_repo_dir", lambda: tmp_path)

        async def mock_run_git(args, cwd=None, timeout=120):
            if args[0] == "fetch":
                return 0, ""
            if args[0] == "checkout":
                return 128, "fatal: could not checkout"
            return 1, "unknown"

        monkeypatch.setattr(su, "_run_git", mock_run_git)

        result = await su.pull_update("origin/master")
        assert result["ok"] is False


# ── update_dependencies ───────────────────────────────────────────────


class TestUpdateDependencies:
    @pytest.mark.asyncio
    async def test_pip_install(self, monkeypatch, tmp_path: Path):
        """update_dependencies should run pip install -e .[worker]."""
        import tinyagentos.worker.self_update as su

        monkeypatch.setattr(su, "_detect_package_manager", lambda: "pip")
        monkeypatch.setattr(su, "_repo_dir", lambda: tmp_path)
        venv_dir = tmp_path / ".venv"
        venv_dir.mkdir()
        (venv_dir / "bin").mkdir()
        (venv_dir / "bin" / "pip").write_text("#!/bin/sh\necho fake pip")
        (venv_dir / "bin" / "pip").chmod(0o755)
        monkeypatch.setattr(su, "_venv_dir", lambda: venv_dir)

        # Mock subprocess for pip
        async def mock_communicate():
            return b"installed ok", b""

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = mock_communicate

        with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=mock_proc)) as mock_exec:
            result = await su.update_dependencies()
            assert result["ok"] is True
            assert result["package_manager"] == "pip"
            mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_pip_not_found(self, monkeypatch, tmp_path: Path):
        """update_dependencies should fail when pip is not found."""
        import tinyagentos.worker.self_update as su

        monkeypatch.setattr(su, "_detect_package_manager", lambda: "pip")
        monkeypatch.setattr(su, "_repo_dir", lambda: tmp_path)
        venv_dir = tmp_path / ".venv"
        venv_dir.mkdir()
        monkeypatch.setattr(su, "_venv_dir", lambda: venv_dir)

        result = await su.update_dependencies()
        assert result["ok"] is False


# ── run_migrations ────────────────────────────────────────────────────


class TestRunMigrations:
    @pytest.mark.asyncio
    async def test_migrations_deferred(self):
        """run_migrations should return ok (deferred to post-restart)."""
        import tinyagentos.worker.self_update as su

        result = await su.run_migrations()
        assert result["ok"] is True


# ── run_full_update integration test ──────────────────────────────────


class TestRunFullUpdate:
    @pytest.mark.asyncio
    async def test_full_update_success_flow(self, monkeypatch, tmp_path: Path):
        """run_full_update should orchestrate all phases and return ok=True."""
        import tinyagentos.worker.self_update as su

        state_dir = tmp_path / "worker-state"
        state_dir.mkdir(parents=True, exist_ok=True)

        # Mock agent
        agent = MagicMock()
        agent.name = "test-worker"
        agent._signing_key = b"fake-key"

        async def mock_heartbeat_ok(*args, **kwargs):
            return 200

        agent.report_update_available = AsyncMock(side_effect=mock_heartbeat_ok)
        agent.initiate_self_drain = AsyncMock(side_effect=mock_heartbeat_ok)
        agent.notify_drain_complete = AsyncMock(side_effect=mock_heartbeat_ok)
        agent.heartbeat = AsyncMock(side_effect=mock_heartbeat_ok)

        # Mock all the sub-operations
        async def mock_checkpoint():
            return {"ok": True, "checkpoint_tag": "tag-123", "git_sha": "from-sha", "output": "tag-123", "exit_code": 0}

        async def mock_pull(target_ref):
            return {"ok": True, "output": "ok", "exit_code": 0}

        async def mock_deps():
            return {"ok": True, "output": "ok", "exit_code": 0, "package_manager": "pip"}

        async def mock_migrations():
            return {"ok": True, "output": "ok", "exit_code": 0}

        async def mock_restart():
            return {"ok": True, "output": "restarted", "exit_code": 0}

        async def mock_git(args, cwd=None, timeout=120):
            if args[0] == "rev-parse":
                return 0, "to-sha-5678\n"
            return 0, ""

        async def short_sleep(*args, **kwargs):
            pass  # Don't actually sleep in tests

        monkeypatch.setattr(su, "create_checkpoint", mock_checkpoint)
        monkeypatch.setattr(su, "pull_update", mock_pull)
        monkeypatch.setattr(su, "update_dependencies", mock_deps)
        monkeypatch.setattr(su, "run_migrations", mock_migrations)
        monkeypatch.setattr(su, "restart_service", mock_restart)
        monkeypatch.setattr(su, "_run_git", mock_git)
        monkeypatch.setattr(su, "_install_dir", lambda: tmp_path)
        monkeypatch.setattr(su, "_repo_dir", lambda: tmp_path)
        monkeypatch.setattr(su, "_venv_dir", lambda: tmp_path)
        monkeypatch.setattr(su, "_write_update_marker", lambda *args: None)
        monkeypatch.setattr(asyncio, "sleep", short_sleep)

        result = await su.run_full_update(
            target_ref="origin/master",
            controller_url="http://localhost:9898",
            agent=agent,
            state_dir=state_dir,
            graceful=False,  # Skip drain wait for test speed
        )

        assert result["ok"] is True
        assert "checkpoint" in result["phases"]
        assert result["phases"]["checkpoint"]["ok"] is True
        assert "pull" in result["phases"]
        assert result["phases"]["pull"]["ok"] is True
        assert "dependencies" in result["phases"]
        assert result["phases"]["dependencies"]["ok"] is True
        assert "restart" in result["phases"]

    @pytest.mark.asyncio
    async def test_full_update_checkpoint_fails(self, monkeypatch, tmp_path: Path):
        """run_full_update should abort when checkpoint fails."""
        import tinyagentos.worker.self_update as su

        state_dir = tmp_path / "worker-state"
        state_dir.mkdir(parents=True, exist_ok=True)

        agent = MagicMock()
        agent.name = "test-worker"

        async def mock_checkpoint():
            return {"ok": False, "checkpoint_tag": "", "git_sha": "", "output": "failed", "exit_code": 1}

        monkeypatch.setattr(su, "create_checkpoint", mock_checkpoint)

        result = await su.run_full_update(
            target_ref="origin/master",
            controller_url="http://localhost:9898",
            agent=agent,
            state_dir=state_dir,
        )
        assert result["ok"] is False
        assert "checkpoint failed" in result["error"]

    @pytest.mark.asyncio
    async def test_full_update_pull_fails(self, monkeypatch, tmp_path: Path):
        """run_full_update should abort when pull fails (no rollback needed)."""
        import tinyagentos.worker.self_update as su

        state_dir = tmp_path / "worker-state"
        state_dir.mkdir(parents=True, exist_ok=True)

        agent = MagicMock()
        agent.name = "test-worker"
        agent.report_update_available = AsyncMock(return_value=200)
        agent.initiate_self_drain = AsyncMock(return_value=200)
        agent.notify_drain_complete = AsyncMock(return_value=200)

        async def mock_checkpoint():
            return {"ok": True, "checkpoint_tag": "tag-123", "git_sha": "from-sha", "output": "tag-123", "exit_code": 0}

        async def mock_pull(target_ref):
            return {"ok": False, "output": "checkout failed", "exit_code": 1}

        monkeypatch.setattr(su, "create_checkpoint", mock_checkpoint)
        monkeypatch.setattr(su, "pull_update", mock_pull)
        monkeypatch.setattr(su, "_install_dir", lambda: tmp_path)
        monkeypatch.setattr(su, "_repo_dir", lambda: tmp_path)
        monkeypatch.setattr(su, "_venv_dir", lambda: tmp_path)
        monkeypatch.setattr(asyncio, "sleep", AsyncMock())

        result = await su.run_full_update(
            target_ref="origin/master",
            controller_url="http://localhost:9898",
            agent=agent,
            state_dir=state_dir,
        )
        assert result["ok"] is False
        assert "pull failed" in result["error"]


# ── post_update_startup ───────────────────────────────────────────────


class TestPostUpdateStartup:
    @pytest.mark.asyncio
    async def test_no_marker_returns_none(self, monkeypatch, tmp_path: Path):
        """post_update_startup should return None when no marker exists."""
        import tinyagentos.worker.self_update as su

        state_dir = tmp_path / "worker-state"
        state_dir.mkdir(parents=True, exist_ok=True)

        agent = MagicMock()

        result = await su.post_update_startup(
            controller_url="http://controller:9898",
            agent=agent,
            state_dir=state_dir,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_health_check_pass(self, monkeypatch, tmp_path: Path):
        """post_update_startup should report success on healthy restart."""
        import tinyagentos.worker.self_update as su

        state_dir = tmp_path / "worker-state"
        state_dir.mkdir(parents=True, exist_ok=True)

        _write_update_marker(state_dir, "tag-123", "from-sha", "to-sha")

        agent = MagicMock()
        agent.name = "test-worker"
        agent._signing_key = b"fake-key"

        async def mock_health_check(timeout=30):
            return {"ok": True, "output": "healthy", "exit_code": 0}

        async def mock_signal(*args, **kwargs):
            return 200

        monkeypatch.setattr(su, "run_health_check", mock_health_check)
        monkeypatch.setattr(su, "signal_update_outcome", mock_signal)
        monkeypatch.setattr(su, "POST_RESTART_GRACE_PERIOD", 0)
        monkeypatch.setattr(su, "clear_update_marker", lambda d: None)

        result = await su.post_update_startup(
            controller_url="http://controller:9898",
            agent=agent,
            state_dir=state_dir,
        )
        assert result is not None
        assert result["ok"] is True
        assert result["outcome"] == "success"

    @pytest.mark.asyncio
    async def test_health_check_fail_triggers_rollback(self, monkeypatch, tmp_path: Path):
        """post_update_startup should rollback on failed health check."""
        import tinyagentos.worker.self_update as su

        state_dir = tmp_path / "worker-state"
        state_dir.mkdir(parents=True, exist_ok=True)

        _write_update_marker(state_dir, "tag-123", "from-sha", "to-sha")

        agent = MagicMock()
        agent.name = "test-worker"
        agent._signing_key = b"fake-key"

        call_log = []

        async def mock_health_check(timeout=30):
            return {"ok": False, "output": "port not listening", "exit_code": 1}

        async def mock_rollback(checkpoint_tag=None):
            call_log.append(("rollback", checkpoint_tag))
            return {"ok": True, "output": "rolled back", "exit_code": 0}

        async def mock_signal(*args, **kwargs):
            call_log.append(("signal", kwargs.get("outcome")))
            return 200

        monkeypatch.setattr(su, "run_health_check", mock_health_check)
        monkeypatch.setattr(su, "rollback_to_checkpoint", mock_rollback)
        monkeypatch.setattr(su, "signal_update_outcome", mock_signal)
        monkeypatch.setattr(su, "POST_RESTART_GRACE_PERIOD", 0)
        monkeypatch.setattr(su, "clear_update_marker", lambda d: None)

        result = await su.post_update_startup(
            controller_url="http://controller:9898",
            agent=agent,
            state_dir=state_dir,
        )
        assert result is not None
        assert result["ok"] is False
        assert result["outcome"] == "rollback"
        assert ("rollback", "tag-123") in call_log
        assert ("signal", "rollback") in call_log

    @pytest.mark.asyncio
    async def test_stale_marker_triggers_rollback_even_when_health_check_passes(
        self, monkeypatch, tmp_path: Path,
    ):
        """A stale marker means the new code never booted — roll back without
        trusting a health check that can only pass when the process is running.

        Regression guard for BLOCKER 3: the health check asserting "service
        active + port listening" is tautologically true whenever the hook runs,
        so it can never catch a failed boot.  The marker's staleness is the
        signal that the update never completed.
        """
        import datetime
        import tinyagentos.worker.self_update as su

        state_dir = tmp_path / "worker-state"
        state_dir.mkdir(parents=True, exist_ok=True)

        # Write a marker whose started_at is long in the past — the new code
        # never came up, and the worker has only now recovered (e.g. after a
        # systemd start-limit give-up and a later restart).
        _write_update_marker(state_dir, "tag-123", "from-sha", "to-sha")
        marker_path = state_dir / "update-in-progress.json"
        marker = json.loads(marker_path.read_text())
        marker["started_at"] = (
            datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(seconds=su.STALE_UPDATE_MARKER_SECONDS + 60)
        ).isoformat()
        marker_path.write_text(json.dumps(marker))

        agent = MagicMock()
        agent.name = "test-worker"
        agent._signing_key = b"fake-key"

        call_log = []

        async def mock_health_check(timeout=30):
            # Health check PASSES — worker is active and listening.  On the old
            # code this would have reported "success" and swallowed the failure.
            call_log.append("health-check")
            return {"ok": True, "output": "healthy", "exit_code": 0}

        async def mock_rollback(checkpoint_tag=None):
            call_log.append(("rollback", checkpoint_tag))
            return {"ok": True, "output": "rolled back", "exit_code": 0}

        async def mock_signal(*args, **kwargs):
            call_log.append(("signal", kwargs.get("outcome")))
            return 200

        monkeypatch.setattr(su, "run_health_check", mock_health_check)
        monkeypatch.setattr(su, "rollback_to_checkpoint", mock_rollback)
        monkeypatch.setattr(su, "signal_update_outcome", mock_signal)
        monkeypatch.setattr(su, "POST_RESTART_GRACE_PERIOD", 0)
        monkeypatch.setattr(su, "clear_update_marker", lambda d: None)

        result = await su.post_update_startup(
            controller_url="http://controller:9898",
            agent=agent,
            state_dir=state_dir,
        )

        assert result is not None
        assert result["ok"] is False
        assert result["outcome"] == "rollback"
        assert result.get("stale_marker") is True
        assert ("rollback", "tag-123") in call_log
        assert ("signal", "rollback") in call_log
        # The health check was never consulted — staleness alone triggered it.
        assert "health-check" not in call_log


# ── pull_update argument validation ───────────────────────────────────


class TestPullUpdateValidation:
    @pytest.mark.asyncio
    async def test_rejects_option_like_ref(self, monkeypatch, tmp_path: Path):
        """A ref that begins with '-' must be rejected before reaching git."""
        import tinyagentos.worker.self_update as su

        monkeypatch.setattr(su, "_repo_dir", lambda: tmp_path)
        calls = []

        async def mock_run_git(args, cwd=None, timeout=120):
            calls.append(list(args))
            return 0, ""

        monkeypatch.setattr(su, "_run_git", mock_run_git)

        result = await su.pull_update("--upload-pack=evil")
        assert result["ok"] is False
        assert "invalid target_ref" in result["output"]
        assert calls == []  # git was never invoked

    @pytest.mark.asyncio
    async def test_rejects_remote_protocol_injection(self, monkeypatch, tmp_path: Path):
        """A remote of the form 'ext::sh -c …' must be rejected (command exec)."""
        import tinyagentos.worker.self_update as su

        monkeypatch.setattr(su, "_repo_dir", lambda: tmp_path)
        calls = []

        async def mock_run_git(args, cwd=None, timeout=120):
            calls.append(list(args))
            return 0, ""

        monkeypatch.setattr(su, "_run_git", mock_run_git)

        result = await su.pull_update("ext::sh -c evil/branch")
        assert result["ok"] is False
        assert calls == []

    @pytest.mark.asyncio
    async def test_plain_branch_fetch_uses_double_dash(self, monkeypatch, tmp_path: Path):
        """The no-slash branch must keep the '--' separator before the ref."""
        import tinyagentos.worker.self_update as su

        monkeypatch.setattr(su, "_repo_dir", lambda: tmp_path)
        calls = []

        async def mock_run_git(args, cwd=None, timeout=120):
            calls.append(list(args))
            if args[0] == "fetch":
                return 0, ""
            if args[0] == "checkout":
                return 0, ""
            if args[0] == "branch":
                return 0, ""
            if args[0] == "rev-parse":
                return 0, "def5678\n"
            return 1, "unknown"

        monkeypatch.setattr(su, "_run_git", mock_run_git)

        result = await su.pull_update("mybranch")
        assert result["ok"] is True
        fetch = [c for c in calls if c[0] == "fetch"][0]
        assert fetch == ["fetch", "--quiet", "origin", "--", "mybranch"]

    @pytest.mark.asyncio
    async def test_slash_branch_fetch_uses_double_dash(self, monkeypatch, tmp_path: Path):
        """The slash branch must keep the '--' separator before the branch."""
        import tinyagentos.worker.self_update as su

        monkeypatch.setattr(su, "_repo_dir", lambda: tmp_path)
        calls = []

        async def mock_run_git(args, cwd=None, timeout=120):
            calls.append(list(args))
            if args[0] == "fetch":
                return 0, ""
            if args[0] == "checkout":
                return 0, ""
            if args[0] == "branch":
                return 0, ""
            if args[0] == "rev-parse":
                return 0, "def5678\n"
            return 1, "unknown"

        monkeypatch.setattr(su, "_run_git", mock_run_git)

        result = await su.pull_update("origin/master")
        assert result["ok"] is True
        fetch = [c for c in calls if c[0] == "fetch"][0]
        assert fetch == ["fetch", "--quiet", "origin", "--", "master"]
