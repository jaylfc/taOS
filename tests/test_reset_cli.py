"""``taos reset --onboarding``: assert through the real AuthManager predicate."""
from __future__ import annotations

import json
import sqlite3

import pytest

from tinyagentos import reset_cli
from tinyagentos.auth import AuthManager


@pytest.fixture(autouse=True)
def controller_down(monkeypatch):
    monkeypatch.setattr(reset_cli, "controller_is_up", lambda port, host="127.0.0.1": False)


def _onboarded(data_dir):
    am = AuthManager(data_dir)
    am.setup_user("admin", "Admin", "", "pass1234!")
    assert AuthManager(data_dir).is_configured() is True
    # state that must SURVIVE --onboarding
    (data_dir / "models").mkdir()
    (data_dir / "models" / "m.gguf").write_bytes(b"weights")
    (data_dir / "apps").mkdir()
    (data_dir / "apps" / "a.txt").write_text("installed")
    (data_dir / "agent_registry.db").write_bytes(b"registry")
    con = sqlite3.connect(data_dir / "desktop.db")
    con.execute("CREATE TABLE desktop_settings (user_id TEXT NOT NULL, key TEXT NOT NULL, "
                "value TEXT NOT NULL DEFAULT '{}', PRIMARY KEY (user_id, key))")
    con.execute("INSERT INTO desktop_settings VALUES ('user', 'pref:setup', '{\"dismissed\": true}')")
    con.execute("INSERT INTO desktop_settings VALUES ('user', 'pref:other', '{\"keep\": 1}')")
    con.commit()
    con.close()


def test_reset_onboarding_rearms_onboarding(tmp_path):
    _onboarded(tmp_path)
    assert reset_cli.reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)]) == 0
    am = AuthManager(tmp_path)
    assert am.is_configured() is False
    assert am.needs_onboarding() is True
    # kept
    assert (tmp_path / "models" / "m.gguf").read_bytes() == b"weights"
    assert (tmp_path / "apps" / "a.txt").exists()
    assert (tmp_path / "agent_registry.db").exists()
    con = sqlite3.connect(tmp_path / "desktop.db")
    keys = {r[0] for r in con.execute("SELECT key FROM desktop_settings")}
    con.close()
    assert keys == {"pref:other"}


def test_backup_holds_removed_files_and_path_is_printed(tmp_path, capsys):
    _onboarded(tmp_path)
    assert reset_cli.reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    backups = list((tmp_path / "backups").glob("reset-*"))
    assert len(backups) == 1
    assert (backups[0] / ".auth_user.json").exists()
    assert (backups[0] / "setup_pref.json").exists()
    assert str(backups[0]) in out


def test_backup_holds_every_setup_pref_scope(tmp_path, capsys):
    """The backup must contain every pref:setup row that is deleted, across
    all scopes (legacy global plus any user-specific scopes), not just one."""
    am = AuthManager(tmp_path)
    am.setup_user("admin", "Admin", "", "pass1234!")
    con = sqlite3.connect(tmp_path / "desktop.db")
    con.execute("CREATE TABLE desktop_settings (user_id TEXT NOT NULL, key TEXT NOT NULL, "
                "value TEXT NOT NULL DEFAULT '{}', PRIMARY KEY (user_id, key))")
    con.execute("INSERT INTO desktop_settings VALUES ('user', 'pref:setup', '{\"dismissed\": true}')")
    con.execute("INSERT INTO desktop_settings VALUES ('alice-uid', 'pref:setup', '{\"dismissed\": false}')")
    con.commit()
    con.close()

    assert reset_cli.reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)]) == 0
    backups = list((tmp_path / "backups").glob("reset-*"))
    assert len(backups) == 1
    rows = json.loads((backups[0] / "setup_pref.json").read_text())
    assert {r["user_id"] for r in rows} == {"user", "alice-uid"}
    assert {r["value"] for r in rows} == {'{"dismissed": true}', '{"dismissed": false}'}

    # Every pref:setup row was deleted, in every scope.
    con = sqlite3.connect(tmp_path / "desktop.db")
    remaining = {r[0] for r in con.execute(
        "SELECT user_id FROM desktop_settings WHERE key = 'pref:setup'")}
    con.close()
    assert remaining == set()


def test_no_backup_flag_writes_no_backup(tmp_path):
    _onboarded(tmp_path)
    assert reset_cli.reset_cli(["--onboarding", "--yes", "--no-backup", "--data-dir", str(tmp_path)]) == 0
    assert not (tmp_path / "backups").exists()


def test_declined_prompt_removes_nothing(tmp_path, monkeypatch):
    _onboarded(tmp_path)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "no")
    assert reset_cli.reset_cli(["--onboarding", "--data-dir", str(tmp_path)]) == 1
    assert AuthManager(tmp_path).is_configured() is True
    assert not (tmp_path / "backups").exists()


def test_refuses_while_controller_is_up_unless_forced(tmp_path, monkeypatch):
    _onboarded(tmp_path)
    monkeypatch.setattr(reset_cli, "controller_is_up", lambda port, host="127.0.0.1": True)
    assert reset_cli.reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)]) == 1
    assert AuthManager(tmp_path).is_configured() is True
    assert reset_cli.reset_cli(["--onboarding", "--yes", "--force", "--data-dir", str(tmp_path)]) == 0
    assert AuthManager(tmp_path).is_configured() is False


def test_onboarding_flag_is_required(tmp_path):
    with pytest.raises(SystemExit) as exc:
        reset_cli.reset_cli(["--yes", "--data-dir", str(tmp_path)])
    assert exc.value.code == 2


def test_main_dispatches_reset_verb(monkeypatch):
    import sys

    from tinyagentos import app as app_mod

    seen = {}

    def fake_reset_cli(argv):
        seen["argv"] = argv
        return 0

    monkeypatch.setattr(reset_cli, "reset_cli", fake_reset_cli)
    monkeypatch.setattr(sys, "argv", ["taos", "reset", "--onboarding", "--yes"])
    with pytest.raises(SystemExit) as exc:
        app_mod.main()
    assert exc.value.code == 0
    assert seen["argv"] == ["--onboarding", "--yes"]

def _seed_user(tmp_path, username="admin"):
    AuthManager(tmp_path).setup_user(username, "Admin", "", "pass1234!")


def test_controller_probe_honours_config_port(tmp_path, monkeypatch):
    """The safety probe must check the port the controller binds (config.yaml), not 6969."""
    _seed_user(tmp_path)
    (tmp_path / "config.yaml").write_text("server:\n  port: 7171\n")
    monkeypatch.delenv("TAOS_PORT", raising=False)
    monkeypatch.delenv("TAOS_HOST", raising=False)
    seen = []

    def probe(port, host="127.0.0.1"):
        seen.append(port)
        return True

    monkeypatch.setattr(reset_cli, "controller_is_up", probe)
    assert reset_cli.reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)]) == 1
    assert seen == [7171]


def test_controller_probe_env_port_beats_config(tmp_path, monkeypatch):
    _seed_user(tmp_path)
    (tmp_path / "config.yaml").write_text("server:\n  port: 7171\n")
    monkeypatch.setenv("TAOS_PORT", "8181")
    seen = []
    monkeypatch.setattr(reset_cli, "controller_is_up", lambda port, host="127.0.0.1": seen.append(port) or True)
    assert reset_cli.reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)]) == 1
    assert seen == [8181]


def test_backup_dir_is_unique_per_reset(tmp_path, monkeypatch):
    """Two resets in the same second must not share (and overwrite) one backup directory."""
    monkeypatch.setattr(reset_cli, "controller_is_up", lambda port, host="127.0.0.1": False)
    monkeypatch.setattr(reset_cli, "_backup_stamp", lambda: "20260101T000000Z")
    _seed_user(tmp_path)
    assert reset_cli.reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)]) == 0
    first = (tmp_path / "backups" / "reset-20260101T000000Z" / ".auth_user.json").read_text()
    _seed_user(tmp_path, "other")
    assert reset_cli.reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)]) == 0
    backups = sorted((tmp_path / "backups").glob("reset-*"))
    assert len(backups) == 2
    assert (backups[0] / ".auth_user.json").read_text() == first


def test_controller_probe_honours_config_host(tmp_path, monkeypatch):
    """A controller bound to one LAN address is not reachable on loopback: probe that address."""
    _seed_user(tmp_path)
    (tmp_path / "config.yaml").write_text("server:\n  host: 192.0.2.5\n  port: 7171\n")
    monkeypatch.delenv("TAOS_PORT", raising=False)
    monkeypatch.delenv("TAOS_HOST", raising=False)
    seen = []
    monkeypatch.setattr(reset_cli, "controller_is_up",
                        lambda port, host="127.0.0.1": seen.append((host, port)) or True)
    assert reset_cli.reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)]) == 1
    assert seen == [("192.0.2.5", 7171)]


def test_controller_probe_env_host_beats_config(tmp_path, monkeypatch):
    _seed_user(tmp_path)
    (tmp_path / "config.yaml").write_text("server:\n  host: 192.0.2.5\n  port: 7171\n")
    monkeypatch.setenv("TAOS_HOST", "192.0.2.9")
    monkeypatch.delenv("TAOS_PORT", raising=False)
    seen = []
    monkeypatch.setattr(reset_cli, "controller_is_up",
                        lambda port, host="127.0.0.1": seen.append((host, port)) or True)
    assert reset_cli.reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)]) == 1
    assert seen == [("192.0.2.9", 6969)]


def test_wildcard_bind_probes_loopback(tmp_path, monkeypatch):
    _seed_user(tmp_path)
    (tmp_path / "config.yaml").write_text("server:\n  host: 0.0.0.0\n")
    monkeypatch.delenv("TAOS_PORT", raising=False)
    monkeypatch.delenv("TAOS_HOST", raising=False)
    seen = []
    monkeypatch.setattr(reset_cli, "controller_is_up",
                        lambda port, host="127.0.0.1": seen.append((host, port)) or True)
    assert reset_cli.reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)]) == 1
    assert seen == [("127.0.0.1", 6969)]


def test_controller_rechecked_before_removal(tmp_path, monkeypatch):
    """A controller that starts after the first probe (prompt wait, backups) stops the reset."""
    _seed_user(tmp_path)
    answers = iter([False, True])
    monkeypatch.setattr(reset_cli, "controller_is_up",
                        lambda port, host="127.0.0.1": next(answers))
    assert reset_cli.reset_cli(["--onboarding", "--yes", "--data-dir", str(tmp_path)]) == 1
    assert AuthManager(tmp_path).is_configured() is True
    assert list((tmp_path / "backups").glob("reset-*/.auth_user.json"))
