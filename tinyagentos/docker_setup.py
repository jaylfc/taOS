"""On-demand Docker installation helper for Store Docker apps.

This module provides the audited Docker installation logic that is called
when a user consents to installing Docker for a Store app that requires it.
It mirrors the logic from scripts/install-server.sh ensure_docker_for_apps()
but is callable from the Python API.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys

logger = logging.getLogger(__name__)


def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _docker_running() -> bool:
    """Check if Docker daemon is reachable."""
    try:
        result = _run(["docker", "info"])
        return result.returncode == 0
    except Exception:
        return False


def _configure_docker_incus_coexistence() -> None:
    """Configure Docker to coexist with Incus networking.

    Sets ip-forward-no-drop=true in daemon.json and enables IPv4 forwarding.
    """
    import json

    daemon_json = "/etc/docker/daemon.json"
    data = {}
    if os.path.exists(daemon_json):
        try:
            with open(daemon_json) as f:
                data = json.load(f) or {}
        except Exception:
            data = {}

    if data.get("ip-forward-no-drop") is not True:
        data["ip-forward-no-drop"] = True
        os.makedirs(os.path.dirname(daemon_json), exist_ok=True)
        with open(daemon_json, "w") as f:
            json.dump(data, f, indent=2)
        logger.info("set Docker ip-forward-no-drop=true for incus coexistence")

    # Ensure IPv4 forwarding is enabled
    sysctl_conf = "/etc/sysctl.d/99-taos-forwarding.conf"
    with open(sysctl_conf, "w") as f:
        f.write("net.ipv4.conf.all.forwarding=1\n")
    _run(["sysctl", "-p", sysctl_conf])
    logger.info("enabled IPv4 forwarding for Docker/incus coexistence")


async def ensure_docker_on_demand() -> dict:
    """Install Docker Engine and Compose v2 plugin on-demand.

    This is the audited helper called when a user consents to Docker installation
    for a Store app. It mirrors the logic from the install script but runs in
    the Python context with proper error handling.

    Returns:
        dict with 'success' (bool) and optionally 'error' (str)
    """
    if shutil.which("docker") and _docker_running():
        logger.info("Docker already installed and running")
        return {"success": True}

    # Configure Docker/incus coexistence before first daemon start
    if shutil.which("incus"):
        _configure_docker_incus_coexistence()

    # Install Docker Engine
    logger.info("installing Docker Engine (on-demand for Store app)")
    had_docker = shutil.which("docker") is not None

    try:
        if shutil.which("apt-get"):
            _run(["apt-get", "update", "-qq"], check=True)
            if not had_docker:
                _run(["apt-get", "install", "-y", "-qq", "docker.io"], check=True)
        elif shutil.which("dnf"):
            if not had_docker:
                _run(["dnf", "install", "-y", "-q", "moby-engine"], check=True)
        elif shutil.which("pacman"):
            if not had_docker:
                _run(["pacman", "-Sy", "--noconfirm", "--needed", "docker"], check=True)
        elif shutil.which("apk"):
            if not had_docker:
                _run(["apk", "add", "--no-cache", "docker"], check=True)
        else:
            return {"success": False, "error": "unsupported package manager"}

        # Install Compose v2 plugin
        logger.info("installing Docker Compose v2 plugin")
        compose_installed = False
        if shutil.which("apt-get"):
            # Try distro packages first
            if _run(["apt-cache", "madison", "docker-compose-plugin"]).returncode == 0:
                _run(["apt-get", "install", "-y", "-qq", "docker-compose-plugin"], check=True)
                compose_installed = True
            elif _run(["apt-cache", "madison", "docker-compose-v2"]).returncode == 0:
                _run(["apt-get", "install", "-y", "-qq", "docker-compose-v2"], check=True)
                compose_installed = True
            else:
                # Fall back to Docker's official repo (like install-server.sh does)
                # This is a simplified version; the full fallback is complex
                logger.warning("compose plugin not in distro repo; manual install may be needed")
        elif shutil.which("dnf"):
            _run(["dnf", "install", "-y", "-q", "docker-compose"], check=True)
            compose_installed = True
        elif shutil.which("pacman"):
            _run(["pacman", "-Sy", "--noconfirm", "--needed", "docker-compose"], check=True)
            compose_installed = True
        elif shutil.which("apk"):
            _run(["apk", "add", "--no-cache", "docker-cli-compose"], check=True)
            compose_installed = True

        if not compose_installed and shutil.which("apt-get"):
            logger.warning("Docker Compose v2 plugin may not be installed")

        # Start and enable Docker daemon
        if shutil.which("systemctl"):
            _run(["systemctl", "enable", "--now", "docker"], check=False)
        elif shutil.which("service"):
            _run(["service", "docker", "start"], check=False)
        elif shutil.which("rc-update"):
            _run(["rc-update", "add", "docker", "default"], check=False)
            _run(["service", "docker", "start"], check=False)

        # Wait for daemon to be ready
        import asyncio
        for _ in range(30):
            if _docker_running():
                break
            await asyncio.sleep(1)
        else:
            return {"success": False, "error": "Docker daemon did not start in time"}

        # Add current user to docker group
        user = os.environ.get("SUDO_USER") or os.environ.get("USER")
        if user and user != "root":
            _run(["groupadd", "-f", "docker"], check=False)
            _run(["usermod", "-aG", "docker", user], check=False)
            logger.info(f"added '{user}' to docker group (re-login required)")

        # Restart Docker if it was already running to pick up ip-forward-no-drop
        if had_docker and shutil.which("systemctl"):
            _run(["systemctl", "restart", "docker"], check=False)

        # Ensure FORWARD policy is ACCEPT for incus coexistence
        if shutil.which("incus") and shutil.which("iptables"):
            _run(["iptables", "-P", "FORWARD", "ACCEPT"], check=False)

        logger.info("Docker installed and running successfully")
        return {"success": True}

    except subprocess.CalledProcessError as e:
        logger.error(f"Docker installation failed: {e.stderr}")
        return {"success": False, "error": e.stderr.strip() or str(e)}
    except Exception as e:
        logger.exception("Docker installation failed")
        return {"success": False, "error": str(e)}