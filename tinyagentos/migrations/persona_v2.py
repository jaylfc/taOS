"""persona_v2 — backfill legacy agent records to the v2 persona shape.

Idempotent: calling migrate_agents twice on the same list produces the same
result. Fields already set by the caller are never overwritten.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def migrate_agents(
    agents: list[dict],
    register_fn: Callable[[str], Any],
) -> list[dict]:
    """Bring legacy agent records up to the v2 persona shape and register each.

    For every agent dict in *agents*:

    - Backfills ``soul_md``, ``agent_md``, ``memory_plugin``,
      ``source_persona_id``, ``migrated_to_v2_personas``, and
      ``display_name`` using ``setdefault`` so existing values are preserved.
    - Calls ``register_fn(agent["name"])`` to ensure the agent exists in the
      taosmd registry.  ``AgentExistsError`` is treated as an idempotent
      success.  Any other exception is logged and swallowed so one bad agent
      does not abort the entire migration.

    Args:
        agents: List of agent dicts to migrate in place.
        register_fn: Callable that accepts an agent name string and registers
            it with the taosmd backend (e.g. ``AgentRegistry.register_agent``
            or a module-level ``register_agent`` wrapper).

    Returns:
        The same list, mutated in place (also returned for convenience).
    """
    from taosmd.agents import AgentExistsError  # local import keeps the dep optional

    for agent in agents:
        agent.setdefault("soul_md", "")
        agent.setdefault("agent_md", "")
        agent.setdefault("memory_plugin", "taosmd")
        agent.setdefault("source_persona_id", None)
        agent.setdefault("migrated_to_v2_personas", False)
        agent.setdefault("display_name", agent["name"])

        try:
            register_fn(agent["name"])
        except AgentExistsError:
            # Already registered — idempotent, nothing to do.
            pass
        except Exception:
            logger.exception(
                "migrate_agents: failed to register agent %r", agent["name"]
            )

    return agents
