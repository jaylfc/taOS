import pytest
from tinyagentos.shortcuts.validation import validate_shortcuts


def test_valid_container_terminal():
    entries = [
        {
            "kind": "container-terminal",
            "label": "Container shell",
            "icon": "terminal",
            "requires_capability": "agent.shell",
        }
    ]
    validate_shortcuts(entries)  # must not raise


def test_valid_tui():
    entries = [
        {
            "kind": "tui",
            "label": "OpenClaw agent",
            "icon": "tui",
            "requires_capability": "agent.terminal",
            "command": "openclaw agent",
        }
    ]
    validate_shortcuts(entries)


def test_valid_dashboard():
    entries = [
        {
            "kind": "dashboard",
            "label": "Gateway dashboard",
            "icon": "dashboard",
            "requires_capability": "agent.dashboard",
            "port": 18789,
            "path": "/",
            "auth": {"type": "none", "token_source": None},
        }
    ]
    validate_shortcuts(entries)


def test_missing_kind_raises():
    with pytest.raises(ValueError, match="missing 'kind'"):
        validate_shortcuts([{"label": "X", "icon": "y", "requires_capability": "chat"}])


def test_unknown_kind_raises():
    with pytest.raises(ValueError, match="unknown shortcut kind"):
        validate_shortcuts(
            [
                {
                    "kind": "magic",
                    "label": "X",
                    "icon": "y",
                    "requires_capability": "chat",
                }
            ]
        )


def test_tui_missing_command_raises():
    with pytest.raises(ValueError, match="'command' is required"):
        validate_shortcuts(
            [
                {
                    "kind": "tui",
                    "label": "X",
                    "icon": "tui",
                    "requires_capability": "agent.terminal",
                }
            ]
        )


def test_dashboard_missing_port_raises():
    with pytest.raises(ValueError, match="'port' is required"):
        validate_shortcuts(
            [
                {
                    "kind": "dashboard",
                    "label": "X",
                    "icon": "dashboard",
                    "requires_capability": "agent.dashboard",
                    "path": "/",
                    "auth": {"type": "none", "token_source": None},
                }
            ]
        )


def test_dashboard_missing_auth_raises():
    with pytest.raises(ValueError, match="'auth' is required"):
        validate_shortcuts(
            [
                {
                    "kind": "dashboard",
                    "label": "X",
                    "icon": "dashboard",
                    "requires_capability": "agent.dashboard",
                    "port": 8080,
                    "path": "/",
                }
            ]
        )


def test_missing_common_fields_raises():
    with pytest.raises(ValueError, match="missing required field"):
        validate_shortcuts(
            [
                {
                    "kind": "container-terminal",
                    "icon": "terminal",
                    "requires_capability": "agent.shell",
                    # label missing
                }
            ]
        )


def test_empty_list_ok():
    validate_shortcuts([])  # must not raise
