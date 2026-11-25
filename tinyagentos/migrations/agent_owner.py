"""agent_owner -- give every ownerless config agent a registry owner.

A per-agent local token acts for the agent's registry owner. A config or
imported agent with no registry row (no ``registry_canonical_id``, or one whose
row is gone) has no owner, so its token has no user and user-scoped routes such
as ``POST /api/chat/messages`` refuse it. This step assigns each such agent to
the install owner (the primary user), creating its registry row and writing
``registry_canonical_id`` into the config entry.

Idempotent: an agent whose registry row exists (whatever its owner or status)
is never touched, so a second run changes nothing.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def assign_ownerless_agents(config, registry, auth_mgr) -> list[str]:
    """Return the names of the agents assigned to the install owner."""
    owner = auth_mgr.get_primary_user() if auth_mgr is not None else None
    if not owner or not owner.get("id"):
        logger.info("agent_owner migration: no install owner yet; nothing assigned")
        return []
    assigned: list[str] = []
    for agent in config.agents:
        name = agent.get("name")
        if not isinstance(agent, dict) or not name:
            continue
        cid = agent.get("registry_canonical_id")
        if cid and await registry.get(cid) is not None:
            continue
        try:
            row = await registry.register(
                framework=agent.get("framework") or "none",
                display_name=agent.get("display_name") or name,
                user_id=owner["id"],
            )
        except Exception:  # noqa: BLE001 - one bad agent must not stop the rest
            logger.exception("agent_owner migration: could not register %r", name)
            continue
        agent["registry_canonical_id"] = row["canonical_id"]
        assigned.append(name)
        logger.warning(
            "agent_owner migration: agent %r had no registry owner; assigned to the install owner (%s)",
            name, row["canonical_id"],
        )
    return assigned
