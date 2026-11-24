"""Tests for systemd unit template rendering (tsk-kzbxia).

Verifies that install-server.sh renders the controller unit without ExecStop
(so update restarts don't drain agents) while taos-pre-shutdown.service
still has the drain command.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = REPO_ROOT / "scripts" / "systemd" / "tinyagentos.service"
PRE_SHUTDOWN = REPO_ROOT / "systemd" / "taos-pre-shutdown.service"


def render_system_unit(install_dir: str, port: int, prefetch: str) -> str:
    """Render the system unit exactly as install-server.sh does."""
    sed_cmd = [
        "sed",
        "-e", f"s|TAOS_USER|taos|g",
        "-e", f"s|TAOS_GROUP|taos|g",
        "-e", f"s|TAOS_INSTALL_DIR|{install_dir}|g",
        "-e", f"s|TAOS_PYTHON|{install_dir}/.venv/bin/python|g",
        "-e", f"s|TAOS_PORT|{port}|g",
        "-e", f"s|__TAOS_PREFETCH_VALUE__|{prefetch}|g",
        str(TEMPLATE),
    ]
    result = subprocess.run(sed_cmd, capture_output=True, text=True, check=True)
    return result.stdout


def render_user_unit(install_dir: str, port: int, prefetch: str, user: str, group: str) -> str:
    """Render the user unit exactly as install-server.sh does."""
    sed_cmd = [
        "sed",
        "-e", f"s|TAOS_USER|{user}|g",
        "-e", f"s|TAOS_GROUP|{group}|g",
        "-e", f"s|TAOS_INSTALL_DIR|{install_dir}|g",
        "-e", f"s|TAOS_PYTHON|{install_dir}/.venv/bin/python|g",
        "-e", f"s|TAOS_PORT|{port}|g",
        "-e", f"s|__TAOS_PREFETCH_VALUE__|{prefetch}|g",
        "-e", "/^User=/d",
        "-e", "/^Group=/d",
        "-e", "s|WantedBy=multi-user.target|WantedBy=default.target|g",
        "-e", "/ExecStartPre/,/|| true'$/d",
        str(TEMPLATE),
    ]
    result = subprocess.run(sed_cmd, capture_output=True, text=True, check=True)
    return result.stdout


def test_system_unit_has_no_execstop() -> None:
    """The rendered system unit must not contain ExecStop directive."""
    rendered = render_system_unit("/opt/taos", 6969, "0")
    # Check for ExecStop= directive (not just the word in comments)
    execstop_lines = [line for line in rendered.splitlines() if line.strip().startswith("ExecStop=")]
    assert not execstop_lines, (
        f"System unit must not have ExecStop= directive, but got: {execstop_lines}"
    )
    # Verify key elements are still present
    assert "ExecStart=" in rendered
    assert "ExecReload=" in rendered
    assert "Restart=always" in rendered
    assert "TimeoutStopSec=45" in rendered


def test_user_unit_has_no_execstop() -> None:
    """The rendered user unit must not contain ExecStop directive."""
    rendered = render_user_unit("/home/user/taos", 6969, "0", "user", "users")
    # Check for ExecStop= directive (not just the word in comments)
    execstop_lines = [line for line in rendered.splitlines() if line.strip().startswith("ExecStop=")]
    assert not execstop_lines, (
        f"User unit must not have ExecStop= directive, but got: {execstop_lines}"
    )
    # Verify key elements are still present
    assert "ExecStart=" in rendered
    assert "ExecReload=" in rendered
    assert "Restart=always" in rendered
    assert "TimeoutStopSec=45" in rendered
    # User unit should not have User/Group
    assert "User=" not in rendered
    assert "Group=" not in rendered
    # User unit should have WantedBy=default.target
    assert "WantedBy=default.target" in rendered


def test_taos_pre_shutdown_has_drain_command() -> None:
    """taos-pre-shutdown.service must still have the drain ExecStart."""
    content = PRE_SHUTDOWN.read_text()
    assert "ExecStart=/usr/local/bin/taos-graceful-stop" in content, (
        f"taos-pre-shutdown.service must have drain command, but got:\n{content}"
    )
    # Verify it's a oneshot service for shutdown targets
    assert "Type=oneshot" in content
    assert "Before=shutdown.target reboot.target halt.target" in content
    assert "WantedBy=shutdown.target reboot.target halt.target" in content


def test_template_has_no_taos_stop_script_placeholder() -> None:
    """The template itself must not contain the TAOS_STOP_SCRIPT placeholder."""
    content = TEMPLATE.read_text()
    assert "TAOS_STOP_SCRIPT" not in content, (
        f"Template must not have TAOS_STOP_SCRIPT placeholder, but got:\n{content}"
    )