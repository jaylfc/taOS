"""Tests for systemd unit template rendering (tsk-kzbxia).

Verifies that the controller unit template has no ExecStop (drain on restart
is handled by taos-pre-shutdown.service for real reboots only).
"""
from __future__ import annotations

import re
from pathlib import Path

SYSTEM_TEMPLATE = Path(__file__).resolve().parents[2] / "scripts" / "systemd" / "tinyagentos.service"
USER_TEMPLATE = Path(__file__).resolve().parents[2] / "systemd" / "tinyagentos.service"
PRE_SHUTDOWN_UNIT = Path(__file__).resolve().parents[2] / "systemd" / "taos-pre-shutdown.service"
INSTALL_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install-server.sh"


def _render_system_template(install_dir: str, port: int) -> str:
    """Render the system unit template the way install-server.sh does."""
    template = SYSTEM_TEMPLATE.read_text()
    # Mimic the sed substitutions from install_linux_systemd_system()
    template = template.replace("TAOS_USER", "taos")
    template = template.replace("TAOS_GROUP", "taos")
    template = template.replace("TAOS_INSTALL_DIR", install_dir)
    template = template.replace("TAOS_PYTHON", f"{install_dir}/.venv/bin/python")
    template = template.replace("TAOS_PORT", str(port))
    template = template.replace("TAOS_STOP_SCRIPT", "/usr/local/bin/taos-graceful-stop")
    template = template.replace("__TAOS_PREFETCH_VALUE__", "0")
    # Inject Environment block (simplified)
    template = re.sub(
        r"^Environment=PYTHONUNBUFFERED=1$",
        "Environment=PYTHONUNBUFFERED=1\nEnvironment=TAOS_HOST=0.0.0.0\nEnvironment=TAOS_PORT="
        + str(port)
        + "\nEnvironment=TAOS_BROWSER_PROXY_PORT=6970\nEnvironment=TAOS_SPA_DIR="
        + install_dir
        + "/static/desktop",
        template,
        flags=re.MULTILINE,
    )
    return template


def _render_user_template(install_dir: str, port: int, user: str, group: str, home: str) -> str:
    """Render the user unit template the way install-server.sh does."""
    template = USER_TEMPLATE.read_text()
    template = template.replace("TAOS_USER", user)
    template = template.replace("TAOS_GROUP", group)
    template = template.replace("TAOS_INSTALL_DIR", install_dir)
    template = template.replace("TAOS_PYTHON", f"{install_dir}/.venv/bin/python")
    template = template.replace("TAOS_PORT", str(port))
    template = template.replace("TAOS_STOP_SCRIPT", f"{home}/.local/bin/taos-graceful-stop")
    template = template.replace("__TAOS_PREFETCH_VALUE__", "0")
    # Remove User=/Group= lines
    template = re.sub(r"^User=.*\n", "", template, flags=re.MULTILINE)
    template = re.sub(r"^Group=.*\n", "", template, flags=re.MULTILINE)
    template = template.replace("WantedBy=multi-user.target", "WantedBy=default.target")
    # Remove ExecStartPre block
    template = re.sub(r"^ExecStartPre=.*\n(?:  .*\n)*", "", template, flags=re.MULTILINE)
    # Inject Environment block
    template = re.sub(
        r"^Environment=PYTHONUNBUFFERED=1$",
        "Environment=PYTHONUNBUFFERED=1\nEnvironment=TAOS_HOST=0.0.0.0\nEnvironment=TAOS_PORT="
        + str(port)
        + "\nEnvironment=TAOS_BROWSER_PROXY_PORT=6970\nEnvironment=TAOS_SPA_DIR="
        + install_dir
        + "/static/desktop",
        template,
        flags=re.MULTILINE,
    )
    return template


def test_system_unit_template_has_no_execstop() -> None:
    """The rendered system unit must not contain ExecStop -- drain on restart
    is not wanted; only taos-pre-shutdown.service drains on real reboot."""
    rendered = _render_system_template("/opt/taos", 6969)
    assert "ExecStop=" not in rendered, (
        f"system unit template must not have ExecStop, but got:\n{rendered}"
    )
    # But it should still have the RuntimeDirectory for the stamp dedupe
    assert "RuntimeDirectory=taos" in rendered
    assert "RuntimeDirectoryPreserve=yes" in rendered


def test_user_unit_template_has_no_execstop() -> None:
    """The rendered user unit must not contain ExecStop."""
    rendered = _render_user_template("/home/user/taos", 6969, "user", "user", "/home/user")
    assert "ExecStop=" not in rendered, (
        f"user unit template must not have ExecStop, but got:\n{rendered}"
    )
    assert "RuntimeDirectory=taos" in rendered
    assert "RuntimeDirectoryPreserve=yes" in rendered


def test_taos_pre_shutdown_unit_still_has_execstart() -> None:
    """taos-pre-shutdown.service must still have the drain command for real reboots."""
    content = PRE_SHUTDOWN_UNIT.read_text()
    assert "ExecStart=/usr/local/bin/taos-graceful-stop" in content, (
        f"taos-pre-shutdown.service must have ExecStart for taos-graceful-stop, but got:\n{content}"
    )
    assert "WantedBy=shutdown.target reboot.target halt.target" in content


def test_install_script_still_installs_graceful_stop() -> None:
    """install-server.sh must still install taos-graceful-stop to /usr/local/bin
    so taos-pre-shutdown.service can call it."""
    content = INSTALL_SCRIPT.read_text()
    assert "/usr/local/bin/taos-graceful-stop" in content
    assert "taos-graceful-stop.sh" in content


def test_install_script_no_longer_substitutes_execstop_in_main_unit() -> None:
    """install-server.sh must not substitute TAOS_STOP_SCRIPT into the main
    unit template (the placeholder and sed line were both removed)."""
    content = INSTALL_SCRIPT.read_text()
    # The sed line for TAOS_STOP_SCRIPT must not exist in the script
    assert "TAOS_STOP_SCRIPT" not in content
    # The template doesn't have the placeholder
    assert "TAOS_STOP_SCRIPT" not in SYSTEM_TEMPLATE.read_text()
    assert "TAOS_STOP_SCRIPT" not in USER_TEMPLATE.read_text()