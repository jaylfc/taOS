"""Routes for agent shortcuts: list (GET) and launch (POST)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from tinyagentos.auth import get_current_user
from tinyagentos.shortcuts.capabilities import user_has_capability
from tinyagentos.shortcuts.tickets import mint_ticket, _GLOBAL_JTI_TRACKER
from tinyagentos.cluster.worker_registry import get_local_worker

router = APIRouter()


def _get_agent_by_name_or_id(request: Request, agent_ref: str) -> dict[str, Any]:
    """Return the agent dict for *agent_ref* (matched by name first, then id) or raise 404.

    Two-pass lookup: match by name first across all agents, fall back to id only if
    no name match exists. Single-pass `name or id` is order-dependent — an agent
    with `id="tom"` could win over a different agent with `name="tom"` depending
    on list order.
    """
    agents = request.app.state.config.agents
    for agent in agents:
        if agent.get("name") == agent_ref:
            return agent
    for agent in agents:
        if agent.get("id") == agent_ref:
            return agent
    raise HTTPException(status_code=404, detail="Agent not found")


def _get_framework_shortcuts(agent: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the shortcuts list from the agent's framework manifest, or []."""
    from tinyagentos.frameworks import FRAMEWORKS
    framework_name = agent.get("framework", "")
    framework = FRAMEWORKS.get(framework_name, {})
    return framework.get("shortcuts", [])


@router.get("/api/agents/{agent_id}/shortcuts")
async def list_shortcuts(
    agent_id: str,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> list[dict[str, Any]]:
    """Return the capability-filtered shortcut list for agent_id.

    Each entry includes idx, kind, label, and icon.
    requires_capability is not exposed to the frontend.
    """
    agent = _get_agent_by_name_or_id(request, agent_id)
    all_shortcuts = _get_framework_shortcuts(agent)

    result = []
    for idx, shortcut in enumerate(all_shortcuts):
        cap = shortcut.get("requires_capability", "")
        if user_has_capability(current_user, cap):
            result.append(
                {
                    "idx": idx,
                    "kind": shortcut["kind"],
                    "label": shortcut["label"],
                    "icon": shortcut["icon"],
                }
            )
    return result


@router.post("/api/agents/{agent_id}/shortcuts/{idx}/launch")
async def launch_shortcut(
    agent_id: str,
    idx: int,
    request: Request,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> dict[str, Any]:
    """Mint a 30-second HMAC ticket for the requested shortcut.

    Returns {redirect_url, expires_in: 30}.
    403 if the user lacks the required capability.
    404 if agent or shortcut idx not found.
    """
    agent = _get_agent_by_name_or_id(request, agent_id)
    all_shortcuts = _get_framework_shortcuts(agent)

    if idx < 0 or idx >= len(all_shortcuts):
        raise HTTPException(status_code=404, detail=f"Shortcut idx {idx} not found")

    shortcut = all_shortcuts[idx]
    cap = shortcut.get("requires_capability", "")
    if not user_has_capability(current_user, cap):
        raise HTTPException(
            status_code=403,
            detail=f"Capability '{cap}' required",
        )

    worker = get_local_worker()
    worker_url: str = worker["worker_url"]
    signing_key: bytes = worker["signing_key"]

    scope = shortcut["kind"]
    _ticket, token = mint_ticket(
        agent_id=agent_id,
        shortcut_idx=idx,
        scope=scope,
        signing_key=signing_key,
        worker_url=worker_url,
        ttl=30,
    )

    redirect_url = f"{worker_url}/redeem?t={token}"
    return {"redirect_url": redirect_url, "expires_in": 30}
