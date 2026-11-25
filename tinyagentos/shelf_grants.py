"""Memory shelf grants the controller issues for the memory service (taOSmd).

The memory service binds every registry caller to the shelves it holds a grant
for.  The records live in the grants store (``agent_shelf_grants``) and are
served in the registry grants feed with their own ``shelf`` field.

Who holds what:

* each deployed agent: ``memory:shelf`` on its own shelf, the name it was
  registered with the memory service under (NOT its canonical_id);
* the OS agent: ``memory:shelf`` on its own shelf;
* the controller service identity: ``memory:shelf`` on ``user-memory``;
* a person who opts in to reading an agent they own: ``memory:read`` on that
  agent's shelf (read only; the memory service honours it for person tokens
  only and revokes it with the grant).

Shelf grants are never ordinary grants: they never satisfy a project gate or
the bus ``a2a_send`` / ``a2a_receive`` checks.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable

logger = logging.getLogger(__name__)

USER_MEMORY_SHELF = "user-memory"
SHELF_SCOPE = "memory:shelf"
READ_SCOPE = "memory:read"


async def backfill_agent_shelf_grants(
    grants: Any, registry: Any, agents: Iterable[dict]
) -> int:
    """Give every deployed agent that has an active registry identity a
    ``memory:shelf`` grant on its OWN shelf.  Idempotent; returns how many
    grants were newly added.

    Runs at start-up so an install upgraded from before shelf grants existed
    keeps its agents' memory working the moment the memory service starts
    enforcing.  Agents that opted out of the memory service
    (``memory_mode == "framework"``), agents with no registry identity, and
    identities that are not active are skipped.
    """
    added = 0
    for agent in agents or []:
        cid = agent.get("registry_canonical_id")
        shelf = agent.get("name")
        if not cid or not shelf or agent.get("memory_mode") == "framework":
            continue
        record = await registry.get(cid)
        if record is None or record.get("status") != "active":
            continue
        have = {g["shelf"] for g in await grants.list_shelf_grants(cid)
                if g["scope"] == SHELF_SCOPE}
        if shelf in have:
            continue
        try:
            await grants.add_shelf_grant(cid, SHELF_SCOPE, shelf)
        except ValueError:
            # A name the memory service would not accept as a shelf cannot be
            # granted; say so rather than skip silently.
            logger.warning("no shelf grant for %s: %r is not a valid shelf name", cid, shelf)
            continue
        added += 1
    return added


async def grant_person_read(grants: Any, person_id: str, agent_shelf: str) -> dict:
    """A person explicitly lets themselves read an owned agent's shelf."""
    return await grants.add_shelf_grant(person_id, READ_SCOPE, agent_shelf)


async def revoke_person_read(grants: Any, person_id: str, agent_shelf: str) -> bool:
    return await grants.revoke_shelf_grant(person_id, READ_SCOPE, agent_shelf)
