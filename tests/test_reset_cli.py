"""Tests for ``taos reset`` CLI (tsk-fsdhuh).

Behavioural assertions use the real ``AuthStore.is_configured()`` predicate,
not file-existence checks, so a test that only proves files were unlinked
cannot pass by accident.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from tinyagentos.app import _reset_cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_config(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "server:\n  host: 0.0.0.0\n  port: 6969\nbackends: []\n"
        "qmd:\n  url: http://localhost:7832\nagents: []\n"
        "metrics:\n  poll_interval: 30\n  retention_days: 30\n"
    )


def _write_users(data_dir: Path, users: list[dict]) -> None:
    (data_dir / ".auth_user.json").write_text(json.dumps({"users": users}))


def _seed_onboarding_files(data_dir: Path) -> None:
    _write_users(data_dir, [{"id": "u1", "username": "jay", "password_hash": "old"}])
    (data_dir / ".auth_password").write_text("legacy")
    (data_dir / ".auth_sessions").write_text("{}")
    for name in [
        "agent_registry.db",
        "auth_requests.db",
        "password_resets.db",
        "agent_scope_requests.db",
        "agent_grants.db",
        "user_shares.db",
        "app_grants.db",
        "license_acceptances.db",
        "agent_model_keys.db",
    ]:
        (data_dir / name).write_bytes(b"")
    desktop_db = data_dir / "desktop.db"
    conn = sqlite3.connect(desktop_db)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS desktop_settings ("
        " user_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL DEFAULT '{}',"
        " PRIMARY KEY (user_id, key))"
    )
    conn.execute(
        "INSERT OR REPLACE INTO desktop_settings (user_id, key, value) VALUES (?, ?, ?)",
        ("user", "pref:setup", '{"dismissed": true}'),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestResetOnboarding:
    def test_onboarding_arms_gate(self, tmp_path: Path):
        _make_minimal_config(tmp_path / "config.yaml")
        _seed_onboarding_files(tmp_path)
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "gemma-4e2b.gguf").write_bytes(b"fake-model")

        rc = _reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)])
        assert rc == 0

        from tinyagentos.auth import AuthManager

        am = AuthManager(tmp_path)
        assert am.is_configured() is False
        assert am.needs_onboarding() is True

    def test_backup_created_and_contains_removed_files(self, tmp_path: Path):
        _make_minimal_config(tmp_path / "config.yaml")
        _seed_onboarding_files(tmp_path)

        rc = _reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)])
        assert rc == 0

        backups = list((tmp_path / "backups").glob("reset-*"))
        assert len(backups) == 1
        backup_dir = backups[0]
        assert (backup_dir / ".auth_user.json").exists()
        assert (backup_dir / "auth_requests.db").exists()

    def test_models_survive_onboarding(self, tmp_path: Path):
        _make_minimal_config(tmp_path / "config.yaml")
        _seed_onboarding_files(tmp_path)
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        model_file = models_dir / "gemma-4e2b.gguf"
        model_file.write_bytes(b"fake-model")

        rc = _reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)])
        assert rc == 0

        assert model_file.exists()
        assert models_dir.exists()

    def test_confirmation_prompt_blocks_without_yes(self, tmp_path: Path):
        _make_minimal_config(tmp_path / "config.yaml")
        _seed_onboarding_files(tmp_path)

        with patch("builtins.input", return_value="n"):
            rc = _reset_cli(["--onboarding", "--data-dir", str(tmp_path)])
        assert rc == 1

        from tinyagentos.auth import AuthManager

        am = AuthManager(tmp_path)
        assert am.is_configured() is True

    def test_no_backup_skips_backup(self, tmp_path: Path):
        _make_minimal_config(tmp_path / "config.yaml")
        _seed_onboarding_files(tmp_path)

        rc = _reset_cli(
            ["--onboarding", "--yes", "--no-backup", "--data-dir", str(tmp_path)]
        )
        assert rc == 0

        assert not (tmp_path / "backups").exists()

    def test_all_mode_clears_everything_except_models_and_installed_apps(
        self, tmp_path: Path
    ):
        _make_minimal_config(tmp_path / "config.yaml")
        _seed_onboarding_files(tmp_path)
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        (models_dir / "gemma-4e2b.gguf").write_bytes(b"fake-model")
        installed_apps_db = tmp_path / "installed_apps.db"
        installed_apps_db.write_bytes(b"")
        extra_db = tmp_path / "metrics.db"
        extra_db.write_bytes(b"")
        extra_json = tmp_path / "torrent_settings.json"
        extra_json.write_bytes(b"{}")

        rc = _reset_cli(["--all", "--yes", "--data-dir", str(tmp_path)])
        assert rc == 0

        from tinyagentos.auth import AuthManager

        am = AuthManager(tmp_path)
        assert am.is_configured() is False
        assert am.needs_onboarding() is True
        assert (tmp_path / "config.yaml").exists()
        assert (tmp_path / "installed.json").exists() is False  # not created by seed
        assert models_dir.exists()
        assert installed_apps_db.exists()
        assert not extra_db.exists()
        assert not extra_json.exists()
