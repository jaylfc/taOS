from __future__ import annotations

"""API routes for Agent Browsers -- persistent Chromium profiles per agent.

All routes live under /api/agent-browsers/.  The router reads
``request.app.state.agent_browsers`` which must be an
``AgentBrowsersManager`` instance.
"""

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


# ------------------------------------------------------------------
# Pydantic models
# ------------------------------------------------------------------

class CreateProfileRequest(BaseModel):
    profile_name: str
    agent_name: str | None = None
    node: str = "local"


class AssignRequest(BaseModel):
    agent_name: str


class MoveRequest(BaseModel):
    node: str


# ------------------------------------------------------------------
# Profiles -- CRUD
# ------------------------------------------------------------------

@router.get("/api/agent-browsers/profiles")
async def list_profiles(request: Request, agent_name: str | None = None):
    """List all browser profiles, optionally filtered by agent_name."""
    mgr = request.app.state.agent_browsers
    profiles = await mgr.list_profiles(agent_name=agent_name)
    return {"profiles": profiles, "count": len(profiles)}


@router.post("/api/agent-browsers/profiles")
async def create_profile(request: Request, body: CreateProfileRequest):
    """Create a new browser profile."""
    mgr = request.app.state.agent_browsers
    try:
        profile = await mgr.create_profile(
            profile_name=body.profile_name,
            agent_name=body.agent_name,
            node=body.node,
        )
        return profile
    except Exception as exc:
        logger.exception("create_profile failed: %s", exc)
        return JSONResponse({"error": str(exc)}, status_code=500)


@router.get("/api/agent-browsers/profiles/{profile_id}")
async def get_profile(request: Request, profile_id: str):
    """Fetch a single profile by id."""
    mgr = request.app.state.agent_browsers
    profile = await mgr.get_profile(profile_id)
    if profile is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return profile


@router.delete("/api/agent-browsers/profiles/{profile_id}")
async def delete_profile(request: Request, profile_id: str):
    """Delete a profile (stops container if running, does NOT delete volume)."""
    mgr = request.app.state.agent_browsers
    deleted = await mgr.delete_profile(profile_id)
    if not deleted:
        return JSONResponse({"error": "not found"}, status_code=404)
    return {"status": "deleted", "id": profile_id}


@router.delete("/api/agent-browsers/profiles/{profile_id}/data")
async def delete_profile_data(request: Request, profile_id: str):
    """Delete the Docker volume backing a profile (irreversible)."""
    mgr = request.app.state.agent_browsers
    await mgr.delete_profile_data(profile_id)
    return {"status": "volume_deleted", "id": profile_id}


# ------------------------------------------------------------------
# Browser lifecycle
# ------------------------------------------------------------------

@router.post("/api/agent-browsers/profiles/{profile_id}/start")
async def start_browser(request: Request, profile_id: str):
    """Start the browser for a profile."""
    mgr = request.app.state.agent_browsers
    ok = await mgr.start_browser(profile_id)
    if not ok:
        return JSONResponse({"error": "failed to start or profile not found"}, status_code=400)
    return {"status": "started", "id": profile_id}


@router.post("/api/agent-browsers/profiles/{profile_id}/stop")
async def stop_browser(request: Request, profile_id: str):
    """Stop the browser for a profile."""
    mgr = request.app.state.agent_browsers
    ok = await mgr.stop_browser(profile_id)
    if not ok:
        return JSONResponse({"error": "profile not found"}, status_code=404)
    return {"status": "stopped", "id": profile_id}


# ------------------------------------------------------------------
# Browser inspection
# ------------------------------------------------------------------

@router.get("/api/agent-browsers/profiles/{profile_id}/screenshot")
async def get_screenshot(request: Request, profile_id: str):
    """Return a PNG screenshot of the browser (CDP, 30s cache)."""
    mgr = request.app.state.agent_browsers
    png = await mgr.get_screenshot(profile_id)
    if png is None:
        return JSONResponse({"error": "screenshot unavailable"}, status_code=404)
    return Response(content=png, media_type="image/png")


@router.get("/api/agent-browsers/{agent_name}/{profile_id}/cookies")
async def get_cookies(request: Request, agent_name: str, profile_id: str, domain: str = ""):
    """Return cookies for a domain from the Chromium cookie store."""
    mgr = request.app.state.agent_browsers
    cookies = await mgr.get_cookies(profile_id, domain)
    return {"cookies": cookies, "domain": domain}


@router.get("/api/agent-browsers/profiles/{profile_id}/login-status")
async def get_login_status(request: Request, profile_id: str):
    """Return per-site login status (x, github, youtube, reddit)."""
    mgr = request.app.state.agent_browsers
    status = await mgr.get_login_status(profile_id)
    return status


# ------------------------------------------------------------------
# Assignment & routing
# ------------------------------------------------------------------

@router.put("/api/agent-browsers/profiles/{profile_id}/assign")
async def assign_agent(request: Request, profile_id: str, body: AssignRequest):
    """Assign a profile to an agent."""
    mgr = request.app.state.agent_browsers
    ok = await mgr.assign_agent(profile_id, body.agent_name)
    if not ok:
        return JSONResponse({"error": "profile not found"}, status_code=404)
    return {"status": "assigned", "id": profile_id, "agent_name": body.agent_name}


@router.put("/api/agent-browsers/profiles/{profile_id}/move")
async def move_to_node(request: Request, profile_id: str, body: MoveRequest):
    """Move a profile to a different cluster node."""
    mgr = request.app.state.agent_browsers
    ok = await mgr.move_to_node(profile_id, body.node)
    if not ok:
        return JSONResponse({"error": "profile not found"}, status_code=404)
    return {"status": "moved", "id": profile_id, "node": body.node}
