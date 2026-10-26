"""``taos desktop enable|disable|run|status|launcher`` -- manage local desktop session

Manages the taOS local desktop session by enabling, disabling, starting, or checking
the status of the taos-kiosk.service systemd unit, and managing the guest-mode
desktop launcher (system-wide .desktop entry).
"""
from __future__ import annotations

import os
import subprocess
import sys
from argparse import ArgumentParser, ArgumentError
from pathlib import Path

from tinyagentos.app import PROJECT_DIR

KIOSK_UNIT = "taos-kiosk.service"
LAUNCHER_SCRIPT = PROJECT_DIR / "scripts" / "desktop-launcher.sh"


def _sudo_hint(sub: str) -> str:
    return f"sudo {os.path.abspath(sys.argv[0])} desktop {sub}"
def _run(cmd: list[str]) -> int:
    """Run a command and return its exit code.

    Use this for all systemctl/bash calls so tests can patch it.
    """
    return subprocess.call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
def _capture(cmd: list[str]) -> str:
    """Run a command and return its stdout as string.

    Use this for reading systemctl output; route all subprocess use through
    patchable helpers.
    """
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.stdout.strip()
def desktop_cli(argv: list[str]) -> int:
    """The taos desktop CLI entry point.

    Mirrors tinyagentos.reset_cli.reset_cli in shape: argparse with
    subcommands enable, disable, run, status, launcher.
    """
    parser = ArgumentParser(
        prog="taos desktop",
        description="Manage the taOS local desktop session",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # --- enable ---
    p_enable = subparsers.add_parser(
        "enable",
        help="Enable the local desktop session on boot",
    )
    p_enable.set_defaults(func=lambda args: _desktop_enable(args))

    # --- disable ---
    p_disable = subparsers.add_parser(
        "disable",
        help="Disable the local desktop session",
    )
    p_disable.set_defaults(func=lambda args: _desktop_disable(args))

    # --- run ---
    p_run = subparsers.add_parser(
        "run",
        help="Start the local desktop session without enabling it",
    )
    p_run.set_defaults(func=lambda args: _desktop_run(args))

    # --- status ---
    p_status = subparsers.add_parser(
        "status",
        help="Check the local desktop session status",
    )
    p_status.set_defaults(func=lambda args: _desktop_status(args))

    # --- launcher ---
    p_launcher = subparsers.add_parser(
        "launcher",
        help="Manage the guest-mode desktop launcher (system-wide .desktop entry)",
    )
    p_launcher.add_argument(
        "action",
        help="Add or remove the launcher (add|remove)",
    )
    p_launcher.set_defaults(func=lambda args: _desktop_launcher(args))

    ns = parser.parse_args(argv)
    return ns.func(ns)
def _desktop_enable(args) -> int:
    """Enable the local desktop session."""
    if os.geteuid() != 0:
        print(f"taos desktop enable needs root: run {_sudo_hint('enable')}", file=sys.stderr)
        return 1

    # Check if unit exists; if not, run kiosk-setup.sh first
    if _run(["systemctl", "cat", KIOSK_UNIT]) != 0:
        setup_script = PROJECT_DIR / "scripts" / "kiosk-setup.sh"
        rc = _run(["bash", str(setup_script)])
        if rc != 0:
            return rc

    # Enable the unit
    rc = _run(["systemctl", "enable", KIOSK_UNIT])

    # Read default target and warn if not graphical.target
    try:
        default_target = _capture(["systemctl", "get-default"])
        if default_target != "graphical.target":
            print(
                f"Warning: session will not start at boot until the default target "
                f"is graphical.target (current: {default_target}).",
                file=sys.stderr,
            )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    return rc
def _desktop_disable(args) -> int:
    """Disable the local desktop session."""
    if os.geteuid() != 0:
        print(f"taos desktop disable needs root: run {_sudo_hint('disable')}", file=sys.stderr)
        return 1

    # Check if unit exists; if not, just return success
    if _run(["systemctl", "cat", KIOSK_UNIT]) != 0:
        print("local desktop is not installed", file=sys.stderr)
        return 0

    # Disable and stop the unit
    return _run(["systemctl", "disable", "--now", KIOSK_UNIT])
def _desktop_run(args) -> int:
    """Start the local desktop session without enabling it."""
    if os.geteuid() != 0:
        print(f"taos desktop run needs root: run {_sudo_hint('run')}", file=sys.stderr)
        return 1

    # Check if unit exists; if not, error
    if _run(["systemctl", "cat", KIOSK_UNIT]) != 0:
        print(
            f"local desktop is not installed: run {_sudo_hint('enable')}",
            file=sys.stderr,
        )
        return 1

    # Start the unit
    return _run(["systemctl", "start", KIOSK_UNIT])
def _desktop_status(args) -> int:
    """Check the local desktop session status."""
    # Check if enabled
    enabled_output = _capture(["systemctl", "is-enabled", KIOSK_UNIT])
    enabled_rc = _run(["systemctl", "is-enabled", KIOSK_UNIT])
    if enabled_rc == 0:
        enabled_status = "enabled"
    elif enabled_rc == 1 and "disabled" in enabled_output:
        enabled_status = "disabled"
    else:
        enabled_status = "not installed"

    # Check if active
    active_output = _capture(["systemctl", "is-active", KIOSK_UNIT])
    active_rc = _run(["systemctl", "is-active", KIOSK_UNIT])
    if active_rc == 0:
        active_status = "active"
    elif active_output.strip() == "inactive":
        active_status = "inactive"
    elif active_rc == 1 and "inactive" in active_output:
        active_status = "inactive"
    else:
        active_status = "unknown"

    print(f"local desktop: {enabled_status}")
    print(f"session: {active_status}")
    return 0
def _desktop_launcher(args) -> int:
    """Manage the guest-mode desktop launcher (system-wide .desktop entry)."""
    if os.geteuid() != 0:
        print("taos desktop launcher needs root: run it with sudo", file=sys.stderr)
        return 1

    action = args.action
    if action == "add":
        script_action = "install"
    elif action == "remove":
        script_action = "remove"
    else:
        print(f"taos desktop launcher: invalid action '{action}' (choose from 'add', 'remove')", file=sys.stderr)
        return 1

    return _run(["bash", str(LAUNCHER_SCRIPT), script_action])
if __name__ == "__main__":
    sys.exit(desktop_cli(sys.argv[1:]))
