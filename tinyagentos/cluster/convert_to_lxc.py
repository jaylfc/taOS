"""Convert a flat-mode taOS install to worker-LXC mode.

Flat mode: bare host runs incus directly; agent containers are at the
host level. Worker-LXC mode: bare host runs ONE privileged LXC named
'taos-worker' with nested incus inside; agent containers live in there.

This module:
  1. Enumerates existing flat-mode agent containers (taos-agent-*).
  2. Stops + deletes them. Memory dirs on shared cluster storage survive.
  3. (CLI calls install-worker.sh fresh to set up the worker LXC.)
  4. Redeploys each agent inside the worker LXC's nested incus.

Invoked from `taos worker convert-to-lxc` in tinyagentos/cli/worker.py.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def list_flat_mode_agents() -> list[dict[str, str]]:
    """Return [{name, state}, ...] for all taos-agent-* containers on the host.

    Calls `incus list --format=csv -c ns` and filters by name prefix.
    Returns [] on incus error.
    """
    proc = subprocess.run(
        ["incus", "list", "--format=csv", "-c", "ns"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        logger.warning("incus list failed: %s", proc.stderr.strip())
        return []
    agents = []
    for line in proc.stdout.splitlines():
        parts = line.split(",")
        if len(parts) < 2:
            continue
        name, state = parts[0], parts[1]
        if name.startswith("taos-agent-"):
            agents.append({"name": name, "state": state})
    return agents


async def _run_async(cmd: list[str]) -> subprocess.CompletedProcess:
    """Run a subprocess command async-safely. Patched in tests."""
    return await asyncio.to_thread(
        subprocess.run, cmd, capture_output=True, text=True, timeout=60,
    )


async def drain_and_delete_agents(agents: list[dict[str, str]]) -> None:
    """Stop running agents, then delete all of them. Memory dirs untouched.

    Memory dirs live on shared cluster storage (per migration spec) and
    are not in the agent's container — they survive deletion.
    """
    for agent in agents:
        if agent["state"] == "RUNNING":
            logger.info("stopping %s", agent["name"])
            r = await _run_async(["incus", "stop", agent["name"]])
            if r.returncode != 0:
                logger.warning("stop %s failed: %s", agent["name"], r.stderr)
    for agent in agents:
        logger.info("deleting %s", agent["name"])
        r = await _run_async(["incus", "delete", "--force", agent["name"]])
        if r.returncode != 0:
            logger.warning("delete %s failed: %s", agent["name"], r.stderr)


async def redeploy_agents(agent_configs: list[dict[str, Any]]) -> None:
    """Redeploy each agent into the new worker LXC's nested incus.

    Each entry is the agent's row from agents.json (name, framework, model,
    plus whatever else DeployRequest accepts). Deploys sequentially —
    parallel deploy could thrash incus.
    """
    from tinyagentos.deployer import deploy_agent, DeployRequest
    for cfg in agent_configs:
        logger.info("redeploying %s", cfg["name"])
        req = DeployRequest(**cfg)
        result = await deploy_agent(req)
        if not result.get("success"):
            logger.error("redeploy %s failed: %s", cfg["name"], result)
        else:
            logger.info("redeployed %s", cfg["name"])
