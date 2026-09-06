import pytest
from tinyagentos.shortcuts.types import (
    ContainerTerminalShortcut,
    DashboardAuth,
    DashboardShortcut,
    ShortcutCommon,
    TokenSource,
    TuiShortcut,
)


def test_container_terminal_shortcut_fields():
    s: ContainerTerminalShortcut = {
        "kind": "container-terminal",
        "label": "Container shell",
        "icon": "terminal",
        "requires_capability": "agent.shell",
    }
    assert s["kind"] == "container-terminal"
    assert s["requires_capability"] == "agent.shell"


def test_tui_shortcut_fields():
    s: TuiShortcut = {
        "kind": "tui",
        "label": "OpenClaw agent",
        "icon": "tui",
        "requires_capability": "agent.terminal",
        "command": "openclaw agent",
    }
    assert s["command"] == "openclaw agent"


def test_dashboard_shortcut_fields():
    auth: DashboardAuth = {
        "type": "bearer",
        "token_source": {
            "kind": "container_file",
            "path": "/root/.openclaw/openclaw.json",
            "json_pointer": "/gateway/auth/token",
        },
    }
    s: DashboardShortcut = {
        "kind": "dashboard",
        "label": "Gateway dashboard",
        "icon": "dashboard",
        "requires_capability": "agent.dashboard",
        "port": 18789,
        "path": "/",
        "auth": auth,
    }
    assert s["port"] == 18789
    assert s["auth"]["type"] == "bearer"


def test_token_source_container_env():
    ts: TokenSource = {"kind": "container_env", "var": "OPENCLAW_TOKEN"}
    assert ts["kind"] == "container_env"


def test_token_source_static():
    ts: TokenSource = {"kind": "static", "value": "dev-token"}
    assert ts["value"] == "dev-token"
