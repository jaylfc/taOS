from __future__ import annotations

import fnmatch
from dataclasses import dataclass

from tinyagentos.mcp.registry import MCPServerStore


@dataclass
class PermissionResult:
    allowed: bool
    reason: str


async def check_permission(
    store: MCPServerStore,
    server_id: str,
    agent_name: str,
    agent_groups: list[str],
    tool: str | None = None,
    resource: str | None = None,
) -> PermissionResult:
    attachments = await store.list_attachments_for_agent(agent_name, agent_groups)
    # Filter to only attachments for this server
    matching = [a for a in attachments if a["server_id"] == server_id]

    if not matching:
        return PermissionResult(allowed=False, reason="no attachment grants access")

    if tool is not None:
        # Find attachments that have a non-empty tool list containing this tool,
        # or have an empty tool list (meaning unrestricted).
        tool_allowed = False
        for att in matching:
            tools = att["allowed_tools"]
            if not tools:
                # Empty list = unrestricted within this attachment
                tool_allowed = True
                break
            if tool in tools:
                tool_allowed = True
                break
        if not tool_allowed:
            return PermissionResult(allowed=False, reason="tool not in allowlist")

    if resource is not None:
        # At least one attachment must allow the resource. Attachments with
        # an empty resource list are unrestricted (allow any resource).
        resource_allowed = False
        for att in matching:
            patterns = att["allowed_resources"]
            if not patterns:
                resource_allowed = True
                break
            if any(fnmatch.fnmatch(resource, p) for p in patterns):
                resource_allowed = True
                break
        if not resource_allowed:
            return PermissionResult(allowed=False, reason="resource pattern mismatch")

    # Determine the reason string based on which attachment matched
    scope_kinds = {a["scope_kind"] for a in matching}
    if "agent" in scope_kinds:
        reason = "allowed via agent attachment"
    elif "group" in scope_kinds:
        reason = "allowed via group attachment"
    else:
        reason = "allowed via all-scope attachment"

    return PermissionResult(allowed=True, reason=reason)
