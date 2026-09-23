"""Offline reset commands (`taos reset --onboarding` and `taos reset --all`).

Verifies the real AuthManager predicate after reset, not just file absence.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tinyagentos.app import _reset_cli
from tinyagentos.auth import AuthManager


def _setup_user(data_dir: Path) -> None:
    mgr = AuthManager(data_dir)
    mgr.setup_user("jay", "Jay", "jay@example.com", "password123")


def test_onboarding_reset_rearms_auth_gate(tmp_path: Path):
    _setup_user(tmp_path)
    assert AuthManager(tmp_path).is_configured() is True

    rc = _reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)])
    assert rc == 0
    assert AuthManager(tmp_path).is_configured() is False
    assert AuthManager(tmp_path).needs_onboarding() is True


def test_all_reset_clears_every_db_except_installed_apps(tmp_path: Path):
    _setup_user(tmp_path)
    (tmp_path / "extra_state.db").write_text("x")
    (tmp_path / "installed_apps.db").write_text("keep-me")

    rc = _reset_cli(["--all", "--yes", "--data-dir", str(tmp_path)])
    assert rc == 0
    assert AuthManager(tmp_path).is_configured() is False
    assert (tmp_path / "installed_apps.db").exists()
    assert not (tmp_path / "extra_state.db").exists()


def test_backup_created_and_contains_removed_files(tmp_path: Path):
    _setup_user(tmp_path)
    (tmp_path / "auth_requests.db").write_text("x")

    rc = _reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)])
    assert rc == 0

    backups = list((tmp_path / "backups").glob("reset-*"))
    assert len(backups) == 1
    backup_dir = backups[0]
    assert backup_dir.is_dir()
    assert (backup_dir / ".auth_user.json").exists()
    assert (backup_dir / "auth_requests.db").exists()


def test_models_and_apps_survive_onboarding_reset(tmp_path: Path):
    _setup_user(tmp_path)
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "qwen3-4b.gguf").write_text("model-bytes")
    apps_dir = tmp_path / "apps"
    apps_dir.mkdir()
    app_dir = apps_dir / "my-app"
    app_dir.mkdir()
    (app_dir / "docker-compose.yaml").write_text("x")
    (tmp_path / "installed_apps.db").write_text("keep-me")

    rc = _reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)])
    assert rc == 0

    assert (models_dir / "qwen3-4b.gguf").exists()
    assert (apps_dir / "my-app" / "docker-compose.yaml").exists()
    assert (tmp_path / "installed_apps.db").exists()


def test_setup_pref_cleared_onboarding(tmp_path: Path):
    _setup_user(tmp_path)
    desktop_db = tmp_path / "desktop.db"
    conn = sqlite3.connect(str(desktop_db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS desktop_settings (user_id TEXT, key TEXT, value TEXT)"
    )
    conn.execute(
        "INSERT INTO desktop_settings VALUES ('user', 'pref:setup', '{\"dismissed\": true}')"
    )
    conn.commit()
    conn.close()

    rc = _reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)])
    assert rc == 0

    conn = sqlite3.connect(str(desktop_db))
    row = conn.execute(
        "SELECT value FROM desktop_settings WHERE user_id = 'user' AND key = 'pref:setup'"
    ).fetchone()
    conn.close()
    assert row is None


def test_confirmation_blocks_without_yes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _setup_user(tmp_path)
    answers = iter(["n"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    rc = _reset_cli(["--onboarding", "--data-dir", str(tmp_path)])
    assert rc == 0
    assert AuthManager(tmp_path).is_configured() is True


def test_no_backup_flag_skips_backup(tmp_path: Path):
    _setup_user(tmp_path)

    rc = _reset_cli(["--onboarding", "--yes", "--no-backup", "--data-dir", str(tmp_path)])
    assert rc == 0

    backups = list((tmp_path / "backups").glob("reset-*"))
    assert len(backups) == 0


def test_force_skips_controller_check(tmp_path: Path):
    _setup_user(tmp_path)
    (tmp_path / "config.yaml").write_text("server:\n  port: 6969\n")

    rc = _reset_cli(["--onboarding", "--yes", "--force", "--data-dir", str(tmp_path)])
    assert rc == 0
    assert AuthManager(tmp_path).is_configured() is False


def test_refuses_when_controller_listening(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    _setup_user(tmp_path)
    (tmp_path / "config.yaml").write_text("server:\n  port: 6969\n")

    class FakeSocket:
        def __enter__(self):
            return self
        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("socket.create_connection", lambda *a, **kw: FakeSocket())

    rc = _reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)])
    assert rc == 1


def test_proceeds_when_controller_not_listening(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    _setup_user(tmp_path)
    (tmp_path / "config.yaml").write_text("server:\n  port: 6969\n")

    def fake_connect(addr, timeout):
        raise ConnectionRefusedError("fake up")

    monkeypatch.setattr("socket.create_connection", fake_connect)

    rc = _reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)])
    assert rc == 0
