from __future__ import annotations

from typing import Any

CAP_CHAT: str = "chat"
CAP_AGENT_SHELL: str = "agent.shell"
CAP_AGENT_TERMINAL: str = "agent.terminal"
CAP_AGENT_DASHBOARD: str = "agent.dashboard"

_ALL_CAPS: frozenset[str] = frozenset(
    {CAP_CHAT, CAP_AGENT_SHELL, CAP_AGENT_TERMINAL, CAP_AGENT_DASHBOARD}
)


def default_caps_for_admin() -> frozenset[str]:
    """Return the full capability set granted to admin / primary users."""
    return _ALL_CAPS


def default_caps_for_new_user() -> frozenset[str]:
    """Return the minimal capability set granted to newly created users."""
    return frozenset({CAP_CHAT})


def user_has_capability(user: dict[str, Any], cap: str) -> bool:
    """Return True if user's capability set contains cap.

    user must have a 'capabilities' key whose value is a set or frozenset.
    If the key is absent, returns False (safe default — deny access).
    """
    caps = user.get("capabilities")
    if caps is None:
        return False
    return cap in caps
