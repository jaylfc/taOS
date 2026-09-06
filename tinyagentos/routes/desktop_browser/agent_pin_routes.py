"""HTTP endpoints for agent pin CRUD."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from tinyagentos.auth import get_current_user
from tinyagentos.routes.desktop_browser import router
from tinyagentos.routes.desktop_browser.agent_pin import (
    AgentNotFoundError,
    TooManyPinsError,
    pin_agent,
    unpin_agent,
)


def _make_agent_exists(request: Request) -> Callable[[str], Awaitable[bool]]:
    """Returns an agent-existence check that uses the app's agent registry.

    Agents live in request.app.state.config.agents — a list of dicts with
    'name' and optional 'id' keys. We treat the agent_id param as matching
    either field so both slug-named and UUID-keyed agents work.
    """
    async def check(agent_id: str) -> bool:
        agents = getattr(request.app.state.config, "agents", [])
        for agent in agents:
            if agent.get("id") == agent_id or agent.get("name") == agent_id:
                return True
        return False
    return check


@router.get("/api/desktop/browser/pins")
async def list_pins_route(
    request: Request,
    profile_id: str,
    tab_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
) -> dict:
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    pins = await request.app.state.browser_store.list_pins_for_tab(
        user_id=user_id, profile_id=profile_id, tab_id=tab_id,
    )
    return {"pins": pins}


class PinRequest(BaseModel):
    profile_id: str
    tab_id: str
    agent_id: str


@router.post("/api/desktop/browser/pins")
async def pin_agent_route(
    request: Request,
    body: PinRequest,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    try:
        inserted = await pin_agent(
            request.app.state.browser_store,
            user_id=user_id,
            profile_id=body.profile_id,
            tab_id=body.tab_id,
            agent_id=body.agent_id,
            agent_exists=_make_agent_exists(request),
        )
    except AgentNotFoundError:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    except TooManyPinsError as e:
        return JSONResponse(
            {"error": f"max {e.args[0]} agents per tab"},
            status_code=400,
        )
    return {"pinned": inserted}


@router.delete("/api/desktop/browser/pins")
async def unpin_agent_route(
    request: Request,
    profile_id: str,
    tab_id: str,
    agent_id: str,
    current_user: dict[str, Any] = Depends(get_current_user),  # noqa: B008
):
    user_id = str(current_user.get("id") or "")
    if not user_id:
        return JSONResponse({"error": "session has no user id"}, status_code=401)
    await unpin_agent(
        request.app.state.browser_store,
        user_id=user_id, profile_id=profile_id, tab_id=tab_id, agent_id=agent_id,
    )
    return Response(status_code=204)  # info-hide: 204 even on miss
