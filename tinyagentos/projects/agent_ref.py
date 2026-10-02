from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRef:
    """Unified reference bridging registry canonical_id and config hex id.

    The dispatcher and task assignment logic need to resolve between two
    identity spaces:
    - Grants/claims use the registry canonical_id (e.g. "grok-20250101-120000")
    - Task.assignee_id for DEPLOYED agents is the config hex id (e.g. "abc123def456")
    - Task.assignee_id for EXTERNAL agents is the canonical_id

    AgentRef provides a single object carrying both identifiers and helpers to
    produce the correct value for each context.
    """

    canonical_id: str
    config_id: str | None
    name: str | None
    deployed: bool
    running: bool

    def aliases(self) -> tuple[str, ...]:
        """Return all identifiers this agent can be looked up by.

        Always includes canonical_id. Includes config_id when present (deployed
        agents registered in config).
        """
        if self.config_id:
            return (self.canonical_id, self.config_id)
        return (self.canonical_id,)

    def assignee_value(self) -> str:
        """Return the value to write as task.assignee_id.

        For deployed agents: the config hex id (routes/delegation.py:_resolve_agent_id).
        For external agents: the canonical_id (no config entry exists).
        """
        if self.deployed and self.config_id:
            return self.config_id
        return self.canonical_id


async def resolve_agent_refs(
    canonical_ids: list[str],
    config,
    registry,
) -> list[AgentRef]:
    """Resolve a list of canonical_ids to AgentRef objects.

    Matches each canonical_id against config.agents[*].registry_canonical_id to
    find the config hex id and name. Drops any id whose registry record is not
    status='active'. NO owner/user_id filter is applied -- holding the grant is
    enough to be assignable (the dispatcher checks grants separately).

    Args:
        canonical_ids: List of registry canonical_ids to resolve.
        config: AppConfig instance with config.agents list.
        registry: AgentRegistryStore instance (or compatible) with async get().

    Returns:
        List of AgentRef for active agents, in the same order as input
        (minus dropped entries).
    """
    if not canonical_ids:
        return []

    # Build lookup: canonical_id -> config agent dict
    config_by_canonical: dict[str, dict] = {}
    for agent in config.agents:
        reg_cid = agent.get("registry_canonical_id")
        if reg_cid:
            config_by_canonical[reg_cid] = agent

    refs: list[AgentRef] = []
    for cid in canonical_ids:
        record = await registry.get(cid)
        if record is None or record.get("status") != "active":
            continue

        config_agent = config_by_canonical.get(cid)
        if config_agent is not None:
            refs.append(
                AgentRef(
                    canonical_id=cid,
                    config_id=config_agent.get("id"),
                    name=config_agent.get("name"),
                    deployed=True,
                    running=False,  # running status is not determined here
                )
            )
        else:
            refs.append(
                AgentRef(
                    canonical_id=cid,
                    config_id=None,
                    name=record.get("display_name"),
                    deployed=False,
                    running=False,
                )
            )
    return refs