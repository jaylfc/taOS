"""Agent tool for discovering the agent frameworks taOS can deploy.

Gives the agent the knowledge to offer the user a concrete next step ("I can
deploy a Hermes agent for you") instead of guessing what is available. Reads the
adapter registry in-process (the same source /api/frameworks serves), so it
stays in sync with what the Deploy Agent wizard shows. Read-only; the deploy
itself stays a user-driven action in the Agents app.
"""
from __future__ import annotations

from fastapi import Request

from tinyagentos.adapters import list_frameworks

LIST_FRAMEWORKS_TOOL = {
    "name": "list_frameworks",
    "description": (
        "List the agent frameworks taOS can deploy (id, name, description, and "
        "verification_status: beta is recommended, alpha is experimental). Use "
        "this before offering to deploy or set up an agent for the user, so you "
        "name a framework that actually exists and prefer a beta one. Read-only; "
        "the user deploys from the Agents app."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "verified_only": {
                "type": "boolean",
                "description": "If true, return only beta (recommended) frameworks. Default false (all).",
            },
        },
    },
}


async def execute_list_frameworks(args: dict, request: Request) -> dict:
    verified_only = bool((args or {}).get("verified_only", False))
    frameworks = list_frameworks()
    if verified_only:
        frameworks = [f for f in frameworks if f.get("verification_status") == "beta"]
    slim = [
        {
            "id": f.get("id"),
            "name": f.get("name"),
            "description": f.get("description"),
            "verification_status": f.get("verification_status"),
        }
        for f in frameworks
    ]
    return {"ok": True, "frameworks": slim, "count": len(slim)}
