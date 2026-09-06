from __future__ import annotations

import logging

from tinyagentos.mcp.permissions import check_permission
from tinyagentos.mcp.registry import MCPServerStore
from tinyagentos.mcp.supervisor import MCPSupervisor

logger = logging.getLogger(__name__)


async def call_tool(
    supervisor: MCPSupervisor,
    store: MCPServerStore,
    agent_name: str,
    agent_groups: list[str],
    server_id: str,
    tool: str,
    arguments: dict,
    resource: str | None = None,
) -> dict:
    result = await check_permission(
        store, server_id, agent_name, agent_groups, tool=tool, resource=resource
    )
    if not result.allowed:
        return {"error": "permission_denied", "reason": result.reason, "status": 403}

    if not supervisor.get_status(server_id)["running"]:
        started = await supervisor.start(server_id)
        if not started:
            return {"error": "server_unavailable", "reason": f"could not start {server_id}", "status": 503}

    # The JSON-RPC transport is not wired yet.  Fail explicitly rather than
    # returning a success-shaped stub: a caller cannot tell `{"ok": True, ...}`
    # from a real tool result, so a stub that reports success silently feeds
    # made-up data into whatever asked for the call.
    logger.warning(
        "mcp proxy: MCP JSON-RPC transport not wired — refusing call "
        "(server=%s tool=%s agent=%s)",
        server_id,
        tool,
        agent_name,
    )
    return {
        "error": "not_implemented",
        "reason": (
            "MCP JSON-RPC transport is not wired yet; no tool call was made"
        ),
        "server_id": server_id,
        "tool": tool,
        "status": 501,
    }
