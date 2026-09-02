"""Agent state versioning API routes.

Exposes git-history operations against each agent container's local state
repo at /root. Container interactions go via ``agent_git`` helpers so the
same code works for both LXC and Docker backends.

Routes
------
GET  /api/agents/{name}/versions            — list commits
GET  /api/agents/{name}/versions/{sha}/diff — show patch for a commit
POST /api/agents/{name}/versions/{sha}/revert — revert to a prior commit

Revert status codes
-------------------
200 {status: "noop"}   — sha is HEAD, nothing to do
200 {status: "reverted"} — success
400                     — invalid sha format
404                     — unknown revision
409                     — sha not an ancestor of HEAD, or dirty tree
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from tinyagentos.agent_db import find_agent
from tinyagentos.agent_git import git_diff, git_is_dirty, git_log, git_merge_base_is_ancestor, git_rev_parse, git_revert

logger = logging.getLogger(__name__)

router = APIRouter()

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")
_REMOTE_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _container_name(agent: dict) -> str:
    remote = agent.get("remote")
    name = agent["name"]
    container = f"taos-agent-{name}"
    if remote:
        if not _REMOTE_RE.match(remote):
            logger.warning("_container_name: skipping invalid remote %r for agent %s", remote, name)
            return container
        return f"{remote}:{container}"
    return container


def _validate_sha(sha: str) -> JSONResponse | None:
    if not _SHA_RE.match(sha):
        return JSONResponse({"error": "invalid sha"}, status_code=400)
    return None


@router.get("/api/agents/{name}/versions")
async def list_versions(request: Request, name: str):
    """Return the commit list for an agent's state repo."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)

    container = _container_name(agent)
    try:
        commits = await git_log(container)
    except Exception as exc:
        logger.warning("versions list failed for %s: %s", name, exc)
        return JSONResponse({"error": "container_unreachable"}, status_code=409)
    return {"agent": name, "versions": commits}


@router.get("/api/agents/{name}/versions/{sha}/diff")
async def version_diff(request: Request, name: str, sha: str):
    """Return the unified diff for a specific commit."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)

    bad = _validate_sha(sha)
    if bad is not None:
        return bad

    container = _container_name(agent)
    try:
        patch = await git_diff(container, sha)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        logger.warning("version diff failed for %s/%s: %s", name, sha, exc)
        return JSONResponse({"error": "container_unreachable"}, status_code=409)
    return {"agent": name, "sha": sha, "diff": patch}


@router.post("/api/agents/{name}/versions/{sha}/revert")
async def revert_version(request: Request, name: str, sha: str):
    """Revert the agent state repo to a prior commit."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)

    bad = _validate_sha(sha)
    if bad is not None:
        return bad

    container = _container_name(agent)
    try:
        head_sha = (await git_rev_parse(container, "HEAD")).strip()
        if sha == head_sha:
            return {"agent": name, "sha": sha, "status": "noop"}
        await git_rev_parse(container, sha)
        if not await git_merge_base_is_ancestor(container, sha):
            return JSONResponse(
                {"error": f"{sha} is not an ancestor of HEAD"},
                status_code=409,
            )
        if await git_is_dirty(container):
            return JSONResponse(
                {"error": "dirty_tree: working tree has uncommitted changes"},
                status_code=409,
            )
        status = await git_revert(container, sha)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        logger.warning("version revert failed for %s/%s: %s", name, sha, exc)
        return JSONResponse({"error": "container_unreachable"}, status_code=409)
    return {"agent": name, "sha": sha, "status": status}
