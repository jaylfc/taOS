from __future__ import annotations

import asyncio
import platform

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.agent_db import find_agent
from tinyagentos.config import save_config_locked
from tinyagentos.frameworks import FRAMEWORKS
from tinyagentos import framework_update as _runner
import tinyagentos.auto_update as _auto_update

router = APIRouter()


def _installed(agent):
    return {"tag": agent.get("framework_version_tag"),
            "sha": agent.get("framework_version_sha")}


def _latest(entry):
    if entry is None:
        return None
    return {"tag": entry["tag"], "sha": entry["sha"],
            "published_at": entry.get("published_at")}


@router.get("/api/agents/{slug}/framework")
async def get_agent_framework(request: Request, slug: str):
    config = request.app.state.config
    agent = find_agent(config, slug)
    if not agent:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    fw_id = agent.get("framework")
    cache = getattr(request.app.state, "latest_framework_versions", {}) or {}
    latest = cache.get(fw_id)
    installed = _installed(agent)
    update_available = bool(
        latest and installed["sha"] and latest["sha"] != installed["sha"]
    )
    return {
        "framework": fw_id,
        "installed": installed,
        "latest": _latest(latest),
        "update_available": update_available,
        "update_status": agent.get("framework_update_status", "idle"),
        "update_started_at": agent.get("framework_update_started_at"),
        "last_error": agent.get("framework_update_last_error"),
        "last_snapshot": agent.get("framework_last_snapshot"),
    }


class UpdateRequest(BaseModel):
    target_version: str | None = None


@router.post("/api/agents/{slug}/framework/update")
async def post_update(request: Request, slug: str, body: UpdateRequest):
    config = request.app.state.config
    agent = find_agent(config, slug)
    if not agent:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    if agent.get("framework_update_status") != "idle":
        return JSONResponse({"error": "agent already updating or in failed state"},
                             status_code=409)
    fw_id = agent.get("framework")
    manifest = FRAMEWORKS.get(fw_id)
    if not manifest or not manifest.get("release_source"):
        return JSONResponse({"error": "agent framework has no update source"},
                             status_code=400)
    cache = getattr(request.app.state, "latest_framework_versions", {}) or {}
    latest = cache.get(fw_id)
    if not latest:
        return JSONResponse({"error": "no latest release cached; try again"},
                             status_code=409)
    if body.target_version and latest["tag"] != body.target_version:
        return JSONResponse(
            {"error": f"target_version {body.target_version!r} does not match latest cached release"},
            status_code=400,
        )

    async def _save():
        await save_config_locked(config, config.config_path)

    asyncio.create_task(_runner.start_update(agent, manifest, latest, save_config=_save))
    return JSONResponse({"status": "accepted", "update_status": "updating"},
                         status_code=202)


@router.get("/api/frameworks/latest")
async def get_latest(request: Request, refresh: bool = False):
    state = request.app.state
    if refresh:
        await _auto_update.poll_frameworks(
            FRAMEWORKS,
            http_client=state.http_client,
            arch=getattr(state, "host_arch", platform.machine()),
            cache=state.latest_framework_versions,
        )
    return state.latest_framework_versions


@router.get("/api/frameworks/slash-commands")
async def slash_commands_manifest(request: Request):
    """Return {slug: [{name, description}, ...]} for every agent in
    app.state.config.agents. Unknown framework or missing slash_commands
    field → empty list. Client (SlashMenu) reads this once per channel
    open and caches for 5 minutes.
    """
    config = getattr(request.app.state, "config", None)
    agents = getattr(config, "agents", []) if config else []
    out: dict[str, list[dict]] = {}
    for a in agents:
        slug = a.get("name")
        if not slug:
            continue
        fw = FRAMEWORKS.get(a.get("framework") or "", {})
        cmds = fw.get("slash_commands") or []
        out[slug] = [{"name": c["name"], "description": c.get("description", "")} for c in cmds]
    return JSONResponse(out)


@router.get("/api/agents/{slug}/slash-commands")
async def agent_slash_commands(request: Request, slug: str):
    """Return the slash commands for a single agent by slug.

    Returns {slug: [{name, description}, ...]} so callers can key
    by slug without transforming the shape.  Unknown agent →
    404, unknown framework → empty command list.
    """
    config = getattr(request.app.state, "config", None)
    agents = getattr(config, "agents", []) if config else []
    agent = next((a for a in agents if a.get("name") == slug), None)
    if agent is None:
        return JSONResponse({"error": "agent not found"}, status_code=404)
    fw = FRAMEWORKS.get(agent.get("framework") or "", {})
    cmds = fw.get("slash_commands") or []
    return JSONResponse({
        slug: [{"name": c["name"], "description": c.get("description", "")} for c in cmds]
    })
