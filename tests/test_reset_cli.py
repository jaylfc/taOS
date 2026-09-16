from __future__ import annotations

import json
import socket
import sqlite3
from pathlib import Path

import pytest

from tinyagentos.app import _reset_cli
from tinyagentos.auth import AuthManager


class FakeSocketConnectFails:
    def settimeout(self, t):
        pass

    def connect(self, addr):
        raise OSError("connection refused")

    def close(self):
        pass


class FakeSocketConnectSucceeds:
    def settimeout(self, t):
        pass

    def connect(self, addr):
        pass

    def close(self):
        pass


class TestResetCli:
    def test_onboarding_reset_rearms_gate(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        monkeypatch.setattr("socket.socket", lambda *a, **kw: FakeSocketConnectFails())

        mgr = AuthManager(tmp_path)
        mgr.setup_user("admin", "Admin", "", "testpass123")
        assert mgr.is_configured() is True
        assert mgr.needs_onboarding() is False

        (tmp_path / "models").mkdir()
        (tmp_path / "models" / "model.bin").write_text("model-data")
        (tmp_path / "apps").mkdir()
        (tmp_path / "apps" / "myapp").mkdir()

        (tmp_path / "desktop.db").touch()
        conn = sqlite3.connect(str(tmp_path / "desktop.db"))
        conn.execute(
            "CREATE TABLE IF NOT EXISTS desktop_settings (user_id TEXT, key TEXT, value TEXT, PRIMARY KEY(user_id, key))"
        )
        conn.execute(
            "INSERT OR REPLACE INTO desktop_settings VALUES (?, ?, ?)",
            ("user", "pref:setup", '{"dismissed": true}'),
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr("builtins.input", lambda _: "y")
        rc = _reset_cli(["--onboarding", "--yes"])

        assert rc == 0
        assert mgr.is_configured() is False
        assert mgr.needs_onboarding() is True
        assert not (tmp_path / ".auth_user.json").exists()
        assert not (tmp_path / ".auth_password").exists()
        assert (tmp_path / "models" / "model.bin").exists()
        assert (tmp_path / "apps" / "myapp").exists()

        conn = sqlite3.connect(str(tmp_path / "desktop.db"))
        row = conn.execute(
            "SELECT value FROM desktop_settings WHERE user_id = ? AND key = ?",
            ("user", "pref:setup"),
        ).fetchone()
        conn.close()
        assert row is None

        captured = capsys.readouterr()
        assert "Backup saved to" in captured.out
        backup_dir = None
        for line in captured.out.splitlines():
            if line.startswith("Backup saved to"):
                backup_dir = Path(line.split("Backup saved to", 1)[1].strip())
        assert backup_dir is not None
        assert backup_dir.exists()
        assert (backup_dir / ".auth_user.json").exists()
        assert (backup_dir / "desktop.db").exists()

    def test_declined_prompt_blocks_deletion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        monkeypatch.setattr("socket.socket", lambda *a, **kw: FakeSocketConnectFails())

        mgr = AuthManager(tmp_path)
        mgr.setup_user("admin", "Admin", "", "testpass123")
        assert mgr.is_configured() is True

        monkeypatch.setattr("builtins.input", lambda _: "n")
        rc = _reset_cli(["--onboarding"])

        assert rc == 1
        assert mgr.is_configured() is True
        assert (tmp_path / ".auth_user.json").exists()

    def test_controller_running_refused_without_force(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))

        mgr = AuthManager(tmp_path)
        mgr.set_password("testpass123")

        monkeypatch.setattr("socket.socket", lambda *a, **kw: FakeSocketConnectSucceeds())
        rc = _reset_cli(["--onboarding", "--yes"])
        assert rc == 1
        assert mgr.is_configured() is True

    def test_all_mode_deletes_mutable_state_but_keeps_models_and_apps(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        monkeypatch.setattr("socket.socket", lambda *a, **kw: FakeSocketConnectFails())

        mgr = AuthManager(tmp_path)
        mgr.setup_user("admin", "Admin", "", "testpass123")
        assert mgr.is_configured() is True

        (tmp_path / "models").mkdir()
        (tmp_path / "models" / "model.bin").write_text("model-data")
        (tmp_path / "apps").mkdir()
        (tmp_path / "apps" / "myapp").mkdir()
        (tmp_path / "config.yaml").write_text("server:\n  port: 6969\n")
        (tmp_path / "metrics.db").write_text("metrics")
        (tmp_path / "chat.db").write_text("chat")

        monkeypatch.setattr("builtins.input", lambda _: "y")
        rc = _reset_cli(["--all", "--yes"])

        assert rc == 0
        assert mgr.is_configured() is False
        assert mgr.needs_onboarding() is True
        assert (tmp_path / "models" / "model.bin").exists()
        assert (tmp_path / "apps" / "myapp").exists()
        assert (tmp_path / "config.yaml").exists()
        assert not (tmp_path / "metrics.db").exists()
        assert not (tmp_path / "chat.db").exists()

        captured = capsys.readouterr()
        assert "Backup saved to" in captured.out
