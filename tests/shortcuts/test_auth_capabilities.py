"""
Verify that the user record carries a capabilities set and that
primary / admin users are seeded with all caps while new users get {chat}.
"""
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
