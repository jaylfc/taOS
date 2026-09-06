"""Remote backend deployment on a TAOS worker.

The controller sends a deploy command via the worker's HTTP endpoint.
The worker agent runs the deploy helper script with sudo (passwordless
via the sudoers drop-in installed by install-worker.sh). The script
handles the actual installation -- the worker agent just shells out
and streams the result back.

Allowed commands are a fixed allowlist so the controller cannot execute
arbitrary shell commands on the worker. The subprocess call uses
asyncio.create_subprocess_exec (not shell=True) with a hardcoded
binary path, so there is no shell injection surface.
"""
from __future__ import annotations

import asyncio
import logging
import shutil

logger = logging.getLogger(__name__)

DEPLOY_HELPER = "/usr/local/bin/taos-deploy-helper"

ALLOWED_COMMANDS = {
    "install-ollama",
    "install-exo",
    "install-llama-cpp",
    "install-llama-cpp --cuda",
    "install-vllm",
    "install-rknpu",
    "update-worker",
    "status",
}


def is_available() -> bool:
    return shutil.which(DEPLOY_HELPER) is not None


async def run_deploy(command: str) -> dict:
    """Run a deploy command via the helper script.

    Returns a dict with keys: ok (bool), command (str), output (str),
    exit_code (int).

    Security: only commands in ALLOWED_COMMANDS are accepted. The
    subprocess is launched via create_subprocess_exec with a fixed
    binary path and no shell interpolation.
    """
    if command not in ALLOWED_COMMANDS:
        return {
            "ok": False,
            "command": command,
            "output": f"command not allowed: {command}",
            "exit_code": -1,
        }

    if not is_available():
        return {
            "ok": False,
            "command": command,
            "output": f"deploy helper not found at {DEPLOY_HELPER}",
            "exit_code": -1,
        }

    args = command.split()
    cmd = ["sudo", DEPLOY_HELPER] + args

    logger.info("deploy: running %s", " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
        output = stdout.decode("utf-8", errors="replace") if stdout else ""
        ok = proc.returncode == 0

        if ok:
            logger.info("deploy: %s completed successfully", command)
        else:
            logger.error(
                "deploy: %s failed (exit %d): %s",
                command, proc.returncode, output[-500:],
            )

        return {
            "ok": ok,
            "command": command,
            "output": output,
            "exit_code": proc.returncode or 0,
        }
    except asyncio.TimeoutError:
        logger.error("deploy: %s timed out after 600s", command)
        return {
            "ok": False,
            "command": command,
            "output": "timed out after 600s",
            "exit_code": -1,
        }
    except Exception as exc:
        logger.error("deploy: %s failed: %s", command, exc)
        return {
            "ok": False,
            "command": command,
            "output": str(exc),
            "exit_code": -1,
        }
