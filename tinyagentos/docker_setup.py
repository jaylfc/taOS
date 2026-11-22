"""On-demand Docker installation helper for Store Docker apps.

This module provides the Docker availability check and returns a
docker_required result with the exact command for the owner to run.
The actual Docker installation must be performed by the owner via the
install script (scripts/install-server.sh ensure_docker_for_apps) with
root privileges, as the taos service account cannot install system packages.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess

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


def _get_install_script_path() -> str | None:
    """Find the install-server.sh script path."""
    # Check common locations
    candidates = [
        "/opt/tinyagentos/scripts/install-server.sh",
        os.path.expanduser("~/tinyagentos/scripts/install-server.sh"),
        "/usr/local/share/taos/scripts/install-server.sh",
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    # Try to find it relative to this file
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(base, "scripts", "install-server.sh")
    if os.path.exists(candidate):
        return candidate
    return None


async def ensure_docker_on_demand() -> dict:
    """Check Docker availability and return docker_required if not installed.

    This function does NOT install Docker - the taos service account lacks
    the necessary privileges. Instead, it returns a docker_required result
    with the exact command the owner must run.

    Returns:
        dict with 'success' (bool), or 'docker_required' (bool) with 'command' (str)
    """
    if shutil.which("docker") and _docker_running():
        logger.info("Docker already installed and running")
        return {"success": True}

    # Honour TAOS_SKIP_DOCKER
    if os.environ.get("TAOS_SKIP_DOCKER", "0") == "1":
        logger.info("TAOS_SKIP_DOCKER=1 - Docker installation skipped")
        return {
            "docker_required": True,
            "command": "",
            "message": "Docker installation is disabled (TAOS_SKIP_DOCKER=1). Store Docker apps are unavailable.",
        }

    # Find the install script to provide the exact command
    script_path = _get_install_script_path()
    if script_path:
        command = f"sudo bash {script_path} --ensure-docker"
    else:
        # Fallback: provide the generic curl command
        command = "curl -fsSL https://raw.githubusercontent.com/jaylfc/taOS/master/scripts/install-server.sh | sudo bash -s -- --ensure-docker"

    logger.info("Docker not available - returning docker_required with command")
    return {
        "docker_required": True,
        "command": command,
        "message": (
            "This app requires Docker to run. Docker must be installed by the system owner "
            "with root privileges. Run the following command to install Docker:\n\n"
            f"  {command}\n\n"
            "This will install Docker Engine, Docker Compose v2 plugin, and configure "
            "Docker to coexist with Incus networking. Set TAOS_SKIP_DOCKER=1 to disable "
            "Docker installation (Store Docker apps will then be unavailable)."
        ),
    }