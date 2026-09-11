"""RED tests for unified data-dir resolution (tsk-4t657i).

Verifies that ``create_app``, the ``recover-password`` CLI, and the five
satellite modules all resolve the data directory through the same precedence
(``data_dir`` param > ``TAOS_DATA_DIR`` env > ``<project>/data``) and that a
conflict between the env var and the explicit param is rejected loudly.
"""
from __future__ import annotations

import json
import os

import pytest

from tinyagentos.app import _recover_password_cli, create_app
from tinyagentos.hub import identity, store as hub_store
from tinyagentos.taosnet import mesh_credentials


def _write_users(data_dir, users):
    (data_dir / ".auth_user.json").write_text(json.dumps({"users": users}))


def _make_app_config(tmp_path):
    import yaml

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()


# ---------------------------------------------------------------------------
# create_app honours TAOS_DATA_DIR
# ---------------------------------------------------------------------------


def test_create_app_honours_taos_data_dir_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    _make_app_config(tmp_path)
    app = create_app()
    assert app.state.data_dir == tmp_path


# ---------------------------------------------------------------------------
# create_app and recover-password agree on the same directory
# ---------------------------------------------------------------------------


def test_create_app_and_recover_password_use_same_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    _make_app_config(tmp_path)
    _write_users(tmp_path, [{"id": "u1", "username": "jay", "password_hash": "old"}])

    app = create_app()
    rc = _recover_password_cli(["--password", "newpassw0rd", "--username", "jay"])
    assert rc == 0
    assert app.state.data_dir == tmp_path


# ---------------------------------------------------------------------------
# Conflicting env and param is rejected
# ---------------------------------------------------------------------------


def test_create_app_rejects_conflicting_env_and_param(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    _make_app_config(tmp_path)
    with pytest.raises(ValueError, match="conflicts with data_dir"):
        create_app(data_dir=tmp_path.parent / "other")


def test_recover_password_cli_rejects_conflicting_env_and_param(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))
    _write_users(tmp_path, [{"id": "u1", "username": "jay", "password_hash": "old"}])
    with pytest.raises(ValueError, match="conflicts with data_dir"):
        _recover_password_cli(
            ["--password", "newpassw0rd", "--username", "jay", "--data-dir", str(tmp_path.parent / "other")]
        )


# ---------------------------------------------------------------------------
# All satellites resolve to the same path
# ---------------------------------------------------------------------------


def test_satellites_all_resolve_to_same_path(tmp_path, monkeypatch):
    monkeypatch.setenv("TAOS_DATA_DIR", str(tmp_path))

    from tinyagentos.app import resolve_data_dir
    from tinyagentos import peer, routes

    expected = resolve_data_dir()

    assert mesh_credentials._data_dir() == expected
    assert identity._data_dir() == expected
    assert hub_store.default_db_path().parent.parent == expected

    assert hasattr(peer, "_data_dir"), "peer._data_dir() not implemented"
    assert peer._data_dir() == expected

    assert hasattr(routes.chat, "_data_dir"), "chat._data_dir() not implemented"
    assert routes.chat._data_dir() == expected
