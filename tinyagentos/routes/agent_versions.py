"""Agent state versioning API routes.

Exposes git-history operations against each agent container's local state
repo at /root. Container interactions go via ``agent_git`` helpers so the
same code works for both LXC and Docker backends.

Scope
-----
The repo root is the agent's home directory, so what these routes can serve
is bounded by an allowlist rather than by the caller: only the state paths in
``agent_git._STATE_PATHS`` (workspace, memory, the per-framework AGENTS.md)
are versioned. Framework config carrying API keys and bridge tokens, shell
history and cache trees are never in history, so ``/diff`` cannot leak them
and ``/revert`` cannot roll the framework install back.

Routes
------
GET  /api/agents/{name}/versions            — list commits
GET  /api/agents/{name}/versions/{sha}/diff — show patch for a commit
POST /api/agents/{name}/versions/{sha}/revert — revert to a prior commit

Revert status codes
-------------------
200 {status: "noop"}   — sha is HEAD, nothing to do
200 {status: "reverted"} — success
400                     — invalid sha format, or an unusable container target
404                     — unknown revision
409                     — sha not an ancestor of HEAD, dirty tree, the git
                          operation failed on repo state, or the container is
                          unreachable
"""
from __future__ import annotations

import logging
import re

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from tinyagentos.agent_db import find_agent
from tinyagentos.agent_git import (
    ContainerUnreachableError,
    DirtyTreeError,
    GitOperationError,
    NotAncestorError,
    git_diff,
    git_log,
    git_rev_parse,
    git_revert,
)
from tinyagentos.auth_context import current_user, require_owner_or_admin

logger = logging.getLogger(__name__)

router = APIRouter()

# Hex object names are case-insensitive to git, and every copy-paste route a
# user has (git log, GitHub, an IDE) can hand over uppercase.
_SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
# Both halves of a container target — the remote and the agent name — have to
# be plain tokens: "remote:container" is the qualified form, so a name that
# smuggles a colon would silently parse as a different remote.
_CONTAINER_TOKEN_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class InvalidContainerTargetError(Exception):
    pass


def _container_name(agent: dict) -> str:
    remote = agent.get("remote")
    name = agent["name"]
    if not _CONTAINER_TOKEN_RE.match(name):
        raise InvalidContainerTargetError(f"invalid agent name {name}")
    container = f"taos-agent-{name}"
    if remote:
        if not _CONTAINER_TOKEN_RE.match(remote):
            raise InvalidContainerTargetError(f"invalid remote {remote} in agent {name}")
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

    user = current_user(request)
    registry = getattr(request.app.state, "agent_registry", None)
    owner_user_id = agent.get("user_id")
    if registry is not None:
        try:
            registry_agent = await registry.get_by_handle(name)
        except RuntimeError:
            registry_agent = None
        if registry_agent is not None:
            require_owner_or_admin(user, registry_agent["user_id"])
            owner_user_id = None
    if owner_user_id:
        require_owner_or_admin(user, owner_user_id)
    elif not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        container = _container_name(agent)
        commits = await git_log(container)
    except InvalidContainerTargetError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
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

    user = current_user(request)
    registry = getattr(request.app.state, "agent_registry", None)
    owner_user_id = agent.get("user_id")
    if registry is not None:
        try:
            registry_agent = await registry.get_by_handle(name)
        except RuntimeError:
            registry_agent = None
        if registry_agent is not None:
            require_owner_or_admin(user, registry_agent["user_id"])
            owner_user_id = None
    if owner_user_id:
        require_owner_or_admin(user, owner_user_id)
    elif not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        container = _container_name(agent)
        patch = await git_diff(container, sha)
    except InvalidContainerTargetError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except DirtyTreeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except NotAncestorError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except ContainerUnreachableError:
        return JSONResponse({"error": "container_unreachable"}, status_code=409)
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

    user = current_user(request)
    registry = getattr(request.app.state, "agent_registry", None)
    owner_user_id = agent.get("user_id")
    if registry is not None:
        try:
            registry_agent = await registry.get_by_handle(name)
        except RuntimeError:
            registry_agent = None
        if registry_agent is not None:
            require_owner_or_admin(user, registry_agent["user_id"])
            owner_user_id = None
    if owner_user_id:
        require_owner_or_admin(user, owner_user_id)
    elif not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")

    try:
        container = _container_name(agent)
        # Resolve the sha (404s an unknown one), then let git_revert decide
        # noop vs reverted *inside* the state lock — comparing against a HEAD
        # read out here races the auto-committer, which would answer "noop"
        # while the tree sits on a commit the caller never asked for.
        resolved_sha = await git_rev_parse(container, sha)
        status = await git_revert(container, resolved_sha)
        return {"agent": name, "sha": resolved_sha, "status": status}
    except InvalidContainerTargetError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except DirtyTreeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except NotAncestorError as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)
    except GitOperationError as exc:
        # The container answered; git could not do the work. Reported apart
        # from container_unreachable so a repo-state problem is diagnosable.
        logger.warning("version revert failed for %s/%s: %s", name, sha, exc)
        return JSONResponse({"error": str(exc)}, status_code=409)
    except ContainerUnreachableError:
        return JSONResponse({"error": "container_unreachable"}, status_code=409)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except Exception as exc:
        logger.warning("version revert failed for %s/%s: %s", name, sha, exc)
        return JSONResponse({"error": "container_unreachable"}, status_code=409)
