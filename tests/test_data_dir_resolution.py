"""RED tests for unified data-dir resolution (tsk-4t657i).

These tests assert that create_app, the recover-password CLI, and all five
satellite modules resolve the data directory from the same source, and that
a mismatch between the explicit argument and TAOS_DATA_DIR is caught at
startup time instead of silently writing to two different directories.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tinyagentos.app import PROJECT_DIR, _recover_password_cli, create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_minimal_config(path: Path) -> None:
    """Write a minimal config.yaml so create_app can load it."""
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.dump(
            {
                "server": {"host": "0.0.0.0", "port": 6969},
                "backends": [],
                "qmd": {"url": "http://localhost:7832"},
                "agents": [],
                "metrics": {"poll_interval": 30, "retention_days": 30},
            }
        )
    )


def _write_users(data_dir: Path, users: list[dict]) -> None:
    (data_dir / ".auth_user.json").write_text(json.dumps({"users": users}))


# ---------------------------------------------------------------------------
# create_app honours TAOS_DATA_DIR
# ---------------------------------------------------------------------------

class TestCreateAppDataDir:
    def test_honours_env_var(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        _make_minimal_config(tmp_path / "config.yaml")
        (tmp_path / ".setup_complete").touch()
        app = create_app()
        assert Path(app.state.data_dir).resolve() == tmp_path.resolve()

    def test_env_and_explicit_disagree_raises(self, monkeypatch, tmp_path):
        explicit = tmp_path / "explicit"
        explicit.mkdir()
        _make_minimal_config(explicit / "config.yaml")
        (explicit / ".setup_complete").touch()
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        with pytest.raises(RuntimeError, match="TAOS_DATA_DIR.*conflicts with"):
            create_app(data_dir=explicit)

    def test_explicit_arg_ignores_env_when_they_match(self, monkeypatch, tmp_path):
        _make_minimal_config(tmp_path / "config.yaml")
        (tmp_path / ".setup_complete").touch()
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        app = create_app(data_dir=tmp_path)
        assert Path(app.state.data_dir).resolve() == tmp_path.resolve()

    def test_falls_back_to_project_data_when_nothing_set(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TAOS_DATA_DIR", raising=False)
        default = PROJECT_DIR / "data"
        _make_minimal_config(default / "config.yaml")
        (default / ".setup_complete").touch()
        app = create_app()
        assert Path(app.state.data_dir).resolve() == default.resolve()


# ---------------------------------------------------------------------------
# recover-password CLI honours TAOS_DATA_DIR
# ---------------------------------------------------------------------------

class TestRecoverPasswordDataDir:
    def test_honours_env_var(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        _make_minimal_config(tmp_path / "config.yaml")
        (tmp_path / ".setup_complete").touch()
        _write_users(tmp_path, [{"id": "u1", "username": "admin", "password_hash": "old"}])
        rc = _recover_password_cli(["--username", "admin", "--password", "newpassw0rd"])
        assert rc == 0
        from tinyagentos.auth import AuthManager
        am = AuthManager(tmp_path)
        assert am.check_password("newpassw0rd", username="admin")[0] is True

    def test_env_and_flag_disagree_raises(self, monkeypatch, tmp_path):
        explicit = tmp_path / "explicit"
        explicit.mkdir()
        _make_minimal_config(explicit / "config.yaml")
        (explicit / ".setup_complete").touch()
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        with pytest.raises(RuntimeError, match="TAOS_DATA_DIR.*conflicts with"):
            _recover_password_cli(
                ["--data-dir", str(explicit), "--username", "admin", "--password", "newpassw0rd"]
            )

    def test_flag_ignores_env_when_they_match(self, monkeypatch, tmp_path):
        _make_minimal_config(tmp_path / "config.yaml")
        (tmp_path / ".setup_complete").touch()
        _write_users(tmp_path, [{"id": "u1", "username": "admin", "password_hash": "old"}])
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        rc = _recover_password_cli(
            ["--data-dir", str(tmp_path), "--username", "admin", "--password", "newpassw0rd"]
        )
        assert rc == 0
        from tinyagentos.auth import AuthManager
        am = AuthManager(tmp_path)
        assert am.check_password("newpassw0rd", username="admin")[0] is True


# ---------------------------------------------------------------------------
# create_app and recover-password agree on TAOS_DATA_DIR
# ---------------------------------------------------------------------------

class TestCreateAppAndRecoverPasswordAgree:
    def test_both_use_same_dir_when_env_set(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        _make_minimal_config(tmp_path / "config.yaml")
        (tmp_path / ".setup_complete").touch()

        app = create_app()
        app_dir = Path(app.state.data_dir).resolve()

        _write_users(tmp_path, [{"id": "u1", "username": "admin", "password_hash": "old"}])
        rc = _recover_password_cli(["--username", "admin", "--password", "newpassw0rd"])
        assert rc == 0

        from tinyagentos.auth import AuthManager
        am = AuthManager(tmp_path)
        assert am.check_password("newpassw0rd", username="admin")[0] is True
        assert app_dir == tmp_path.resolve()


# ---------------------------------------------------------------------------
# Satellites resolve to the same path
# ---------------------------------------------------------------------------

class TestSatelliteResolution:
    def test_all_satellites_match_resolve_data_dir(self, monkeypatch, tmp_path):
        from tinyagentos.app import resolve_data_dir
        from tinyagentos.taosnet import mesh_credentials
        from tinyagentos.hub import identity, store
        from tinyagentos import peer
        from tinyagentos.routes import chat as chat_mod

        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))

        expected = resolve_data_dir()

        assert mesh_credentials._data_dir() == expected
        assert identity._data_dir() == expected
        assert store.default_db_path().parent.parent == expected
        assert Path(os.environ.get("TAOS_DATA_DIR", "./data")) == expected

    def test_peer_resolves_hub_dir_from_env(self, monkeypatch, tmp_path):
        from tinyagentos.hub import identity as identity_mod
        from tinyagentos import peer

        monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
        identity_mod.clear()
        identity_mod.load_or_create()
        (tmp_path / "hub").mkdir(exist_ok=True)

        import sqlite3
        hub_db = tmp_path / "hub" / "hub.db"
        conn = sqlite3.connect(str(hub_db))
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS hub_authors (
                fingerprint       TEXT PRIMARY KEY,
                username          TEXT,
                signing_pubkey    TEXT,
                encryption_pubkey TEXT,
                updated_at        REAL NOT NULL
            );
        """)
        fp = identity_mod.signing_fingerprint()
        conn.execute(
            "INSERT OR REPLACE INTO hub_authors (fingerprint, username, signing_pubkey, encryption_pubkey, updated_at) VALUES (?, ?, ?, ?, ?)",
            (fp, "testuser", "pk", "ek", 1.0),
        )
        conn.commit()
        conn.close()

        result = peer.resolve_local_identity_id()
        assert result == "hub:testuser"
