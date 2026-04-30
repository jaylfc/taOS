"""
Verify that the user record carries a capabilities set and that
primary / admin users are seeded with all caps while new users get {chat}.
Also verifies that legacy records without a capabilities key are migrated
on read rather than silently returning an empty list.
"""
import json

from tinyagentos.auth import AuthManager
from tinyagentos.shortcuts.capabilities import (
    CAP_AGENT_DASHBOARD,
    CAP_AGENT_SHELL,
    CAP_AGENT_TERMINAL,
    CAP_CHAT,
)


def test_primary_user_has_all_capabilities(tmp_path):
    """Primary user (admin / first setup) must have all four capabilities."""
    mgr = AuthManager(tmp_path)
    mgr.setup_user("admin", "Admin", "", "adminpass")
    user = mgr.get_primary_user()
    caps = set(user.get("capabilities", []))
    assert CAP_CHAT in caps
    assert CAP_AGENT_SHELL in caps
    assert CAP_AGENT_TERMINAL in caps
    assert CAP_AGENT_DASHBOARD in caps


def test_new_user_has_only_chat_capability(tmp_path):
    """Users created via the normal invite flow must default to {chat}."""
    mgr = AuthManager(tmp_path)
    mgr.setup_user("admin", "Admin", "", "adminpass")
    code = mgr.add_user_invite("testcap_user", "admin")
    user = mgr.complete_invite("testcap_user", code, "Test Cap", "", "hunter2")
    caps = set(user.get("capabilities", []))
    assert caps == {"chat"}


def test_capabilities_persisted_on_user_record(tmp_path):
    """capabilities key must survive a round-trip through the user store."""
    mgr = AuthManager(tmp_path)
    mgr.setup_user("admin", "Admin", "", "adminpass")
    code = mgr.add_user_invite("testcap_persist", "admin")
    user = mgr.complete_invite("testcap_persist", code, "Test Persist", "", "hunter2")
    fetched = mgr.get_user_by_id(user["id"])
    assert "capabilities" in fetched
    assert set(fetched["capabilities"]) == {"chat"}


def test_legacy_primary_user_without_capabilities_gets_admin_defaults(tmp_path):
    """A legacy user record without a capabilities key (pre-shortcuts install)
    must be projected with admin caps (all four) for the primary user."""
    user_file = tmp_path / ".auth_user.json"
    user_file.write_text(json.dumps({
        "users": [
            {
                "id": "legacy-001",
                "username": "legacy_admin",
                "full_name": "Legacy Admin",
                "email": "",
                "password_hash": "salt:hash",
                "is_admin": True,
                "created_at": "2024-01-01T00:00:00",
                "last_login_at": None,
                # No "capabilities" key — simulates a pre-shortcuts install.
            }
        ],
        "current_user_id": "legacy-001",
    }))
    mgr = AuthManager(tmp_path)
    user = mgr.get_primary_user()
    assert user is not None
    caps = set(user["capabilities"])
    assert CAP_CHAT in caps
    assert CAP_AGENT_SHELL in caps
    assert CAP_AGENT_TERMINAL in caps
    assert CAP_AGENT_DASHBOARD in caps


def test_legacy_non_primary_user_without_capabilities_gets_chat_only(tmp_path):
    """A legacy secondary user record without capabilities must default to {chat}
    so they are not silently locked out of the basic shortcut."""
    user_file = tmp_path / ".auth_user.json"
    user_file.write_text(json.dumps({
        "users": [
            {
                "id": "primary-001",
                "username": "admin",
                "full_name": "Admin",
                "email": "",
                "password_hash": "salt:hash",
                "is_admin": True,
                "capabilities": [CAP_CHAT, CAP_AGENT_SHELL, CAP_AGENT_TERMINAL, CAP_AGENT_DASHBOARD],
            },
            {
                "id": "legacy-002",
                "username": "legacy_user",
                "full_name": "Legacy User",
                "email": "",
                "password_hash": "salt:hash",
                "is_admin": False,
                "created_at": "2024-01-01T00:00:00",
                "last_login_at": None,
                # No "capabilities" key.
            },
        ],
        "current_user_id": "primary-001",
    }))
    mgr = AuthManager(tmp_path)
    user = mgr.get_user_by_id("legacy-002")
    assert user is not None
    caps = set(user["capabilities"])
    assert caps == {CAP_CHAT}
