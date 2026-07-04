"""Maps a skill/tool id to the action class the policy layer governs on.

Kept as a plain dict: a tool not listed here has no action class and is never
gated by the policy layer (``classify`` returns ``None``, and ``execute_skill``
treats that as unconditional allow). Only tools with real-world blast radius
(host code execution, outbound network, deploys, writes outside the agent's
own workspace, spend) are classified.
"""

from __future__ import annotations

# Sensitive action classes that require approval under the CONSERVATIVE
# default posture (see governance/policy_store.py). Everything else defaults
# to "allow" by the absence of a policy row.
SENSITIVE_ACTION_CLASSES = frozenset(
    {"code-exec", "external-network", "deploy", "file-write-external", "spend"}
)

# tool/skill id -> action_class. file_write/file_read/list_files are scoped to
# the agent's own workspace by execute_skill (see _resolve_agent_workspace),
# so they are not classified as file-write-external. web_search is a read-only
# lookup with no host/write blast radius, so it is intentionally NOT classified
# (always allowed) even though its transport happens to be network.
TOOL_ACTION_CLASSES: dict[str, str] = {
    "code_exec": "code-exec",
    "http_request": "external-network",
}


def classify(skill_id: str) -> str | None:
    """Return the action class for a skill id, or None if it is unclassified
    (unclassified skills are always allowed by the policy layer)."""
    return TOOL_ACTION_CLASSES.get(skill_id)
