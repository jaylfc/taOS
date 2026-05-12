"""One-shot token migration — issues per-agent API tokens for pre-existing agents.

Runs on taOS startup after the upgrade that introduces tokens. Idempotent:
agents with `has_token == True` are skipped. Only LXC-backed deployments are
handled in Pass 1 — Docker and Apple Containerization injection paths land
in Pass 2 (those agents fall back to existing auth in the meantime, no
regression).

The container runtime is global, not per-agent: detected once at startup
and exposed via `tinyagentos.containers.backend.get_backend()`. We check
the backend's class name to avoid importing LXCBackend here (keeps the
migration module thin and free of container deps).
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def run_agent_token_migration(
    *,
    agents: list[dict],
    agent_tokens_store: Any,
    container_backend: Any,
) -> dict[str, int]:
    """Issue tokens for LXC agents that don't have one. Returns counts dict."""
    is_lxc = (
        container_backend is not None
        and container_backend.__class__.__name__ == "LXCBackend"
    )
    if not is_lxc:
        n = len(agents)
        logger.info(
            "agent_token_migration: backend is %s, not LXC — skipping all %d agent(s); "
            "Docker/Apple env-injection lands in Pass 2.",
            type(container_backend).__name__ if container_backend else "None",
            n,
        )
        return {"issued": 0, "skipped_has_token": 0, "skipped_non_lxc": n}

    issued = 0
    skipped_has_token = 0
    for agent in agents:
        name = agent.get("name")
        if not name:
            continue
        if await agent_tokens_store.has_token(name):
            skipped_has_token += 1
            continue
        user_id = agent.get("user_id", "default")
        scope = agent.get("scope", ["*"])
        plaintext, _ = await agent_tokens_store.issue(
            agent_id=name, user_id=user_id, scope=scope
        )
        container = f"taos-agent-{name}"
        env_result = await container_backend.set_env(container, "TAOS_TOKEN", plaintext)
        if not env_result.get("success", False):
            # Roll back the issuance so subsequent runs retry instead of
            # silently skipping. Without this, the agent ends up with a DB
            # token but no TAOS_TOKEN env var and can never authenticate.
            await agent_tokens_store.revoke_for_agent(name)
            logger.warning(
                "agent_token_migration: set_env failed for %s, revoked issuance: %s",
                name,
                env_result.get("output", ""),
            )
            continue
        logger.info("agent_token_migration: issued token for agent %s", name)
        issued += 1

    summary = {
        "issued": issued,
        "skipped_has_token": skipped_has_token,
        "skipped_non_lxc": 0,
    }
    logger.info("agent_token_migration: %s", summary)
    return summary
