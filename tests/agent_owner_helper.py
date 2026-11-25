"""Give a config agent a registry owner, as a real deploy does.

A per-agent local token acts for the agent's registry OWNER; an agent with no
owner has no user, so user-scoped routes refuse it. Tests that exercise an
agent token on such routes must attribute the agent first.
"""
from __future__ import annotations


async def own_agent(app, name: str, user_id: str) -> str:
    registry = app.state.agent_registry
    if registry._db is None:
        await registry.init()
    row = await registry.register(framework="claude-code", display_name=name, user_id=user_id)
    for agent in app.state.config.agents:
        if agent.get("name") == name:
            agent["registry_canonical_id"] = row["canonical_id"]
            break
    else:
        app.state.config.agents.append({"name": name, "registry_canonical_id": row["canonical_id"]})
    return row["canonical_id"]
