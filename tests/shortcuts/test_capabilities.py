import pytest
from tinyagentos.shortcuts.capabilities import (
    CAP_AGENT_DASHBOARD,
    CAP_AGENT_SHELL,
    CAP_AGENT_TERMINAL,
    CAP_CHAT,
    default_caps_for_admin,
    default_caps_for_new_user,
    user_has_capability,
)


def test_cap_constants_are_strings():
    assert CAP_CHAT == "chat"
    assert CAP_AGENT_SHELL == "agent.shell"
    assert CAP_AGENT_TERMINAL == "agent.terminal"
    assert CAP_AGENT_DASHBOARD == "agent.dashboard"


def test_default_caps_for_admin_includes_all():
    caps = default_caps_for_admin()
    assert CAP_CHAT in caps
    assert CAP_AGENT_SHELL in caps
    assert CAP_AGENT_TERMINAL in caps
    assert CAP_AGENT_DASHBOARD in caps


def test_default_caps_for_new_user_is_chat_only():
    caps = default_caps_for_new_user()
    assert caps == {CAP_CHAT}


def test_user_has_capability_true():
    user = {"capabilities": {CAP_CHAT, CAP_AGENT_SHELL}}
    assert user_has_capability(user, CAP_AGENT_SHELL) is True


def test_user_has_capability_false():
    user = {"capabilities": {CAP_CHAT}}
    assert user_has_capability(user, CAP_AGENT_SHELL) is False


def test_user_has_capability_missing_key_returns_false():
    user = {}
    assert user_has_capability(user, CAP_CHAT) is False


def test_default_caps_for_admin_returns_frozenset_or_set():
    caps = default_caps_for_admin()
    assert isinstance(caps, (set, frozenset))


def test_default_caps_for_new_user_returns_frozenset_or_set():
    caps = default_caps_for_new_user()
    assert isinstance(caps, (set, frozenset))
