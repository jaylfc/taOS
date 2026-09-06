"""Offline local-account recovery (`taos recover-password`).

Verifies AuthManager.recover_password force-sets a verifiable password across
the store shapes it must handle: a single-user record, a named user in a
multi-user store, a pending (invite) user, and the legacy password file.
"""
from __future__ import annotations

import json

import pytest

from tinyagentos.auth import AuthManager


def _write_users(data_dir, users):
    (data_dir / ".auth_user.json").write_text(json.dumps({"users": users}))


def test_single_user_recover(tmp_path):
    _write_users(tmp_path, [{"id": "u1", "username": "jay", "password_hash": "old"}])
    am = AuthManager(tmp_path)
    who = am.recover_password("newpassw0rd")
    assert who == "jay"
    ok, rec = am.check_password("newpassw0rd", username="jay")
    assert ok and rec["username"] == "jay"


def test_multi_user_requires_username(tmp_path):
    _write_users(
        tmp_path,
        [
            {"id": "u1", "username": "jay", "password_hash": "a"},
            {"id": "u2", "username": "sam", "password_hash": "b"},
        ],
    )
    am = AuthManager(tmp_path)
    with pytest.raises(ValueError):
        am.recover_password("newpassw0rd")  # ambiguous without --username


def test_multi_user_named(tmp_path):
    _write_users(
        tmp_path,
        [
            {"id": "u1", "username": "jay", "password_hash": "a"},
            {"id": "u2", "username": "sam", "password_hash": "b"},
        ],
    )
    am = AuthManager(tmp_path)
    who = am.recover_password("newpassw0rd", username="sam")
    assert who == "sam"
    assert am.check_password("newpassw0rd", username="sam")[0] is True
    # the other user is untouched
    assert am.check_password("newpassw0rd", username="jay")[0] is False


def test_unknown_username_errors(tmp_path):
    _write_users(tmp_path, [{"id": "u1", "username": "jay", "password_hash": "a"}])
    with pytest.raises(ValueError):
        AuthManager(tmp_path).recover_password("newpassw0rd", username="ghost")


def test_pending_user_becomes_active(tmp_path):
    _write_users(tmp_path, [{"id": "u1", "username": "jay", "pending_invite": "code123"}])
    am = AuthManager(tmp_path)
    who = am.recover_password("newpassw0rd")
    assert who == "jay"
    stored = json.loads((tmp_path / ".auth_user.json").read_text())["users"][0]
    assert "pending_invite" not in stored
    assert am.check_password("newpassw0rd", username="jay")[0] is True


def test_legacy_password_store(tmp_path):
    # No users list -> legacy single-password file.
    am = AuthManager(tmp_path)
    who = am.recover_password("newpassw0rd")
    assert who == "(legacy)"
    assert (tmp_path / ".auth_password").exists()
    assert am.check_password("newpassw0rd")[0] is True
