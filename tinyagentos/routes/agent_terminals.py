"""Persistent agent terminal lifecycle routes (#617).

  GET    /api/agents/{name}/terminals
  POST   /api/agents/{name}/terminals/{session}/restart
  DELETE /api/agents/{name}/terminals/{session}

The shortcut terminal WebSocket (routes/shortcut_proxy.py) runs each
terminal shortcut inside a tmux session named
``taos-term-<agent>-<kind>-<idx>`` in the agent's container, so closing the
window detaches instead of killing the shell. These routes are the backend
for the agent settings screen's 'running terminals' list: list the live
sessions, restart one (kill + fresh session for the same shortcut), or kill
one outright.

{name} is the same agent identifier the terminal WebSocket route is opened
with. Auth matches the neighbouring agent routes: the agent's owner or an
admin, with per-agent local tokens fenced to their own agent. A {session}
that does not parse as one of *this* agent's terminal sessions is a 404 and
never reaches tmux, so a crafted name cannot target another agent's (or a
non-terminal) session.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from tinyagentos.auth_context import (
    CurrentUser,
    current_user,
    require_agent_owner_or_admin,
)
from tinyagentos.containers import exec_in_container
from tinyagentos.routes.shortcut_proxy import (
    parse_terminal_session,
    tmux_session_cmd,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_LIST_FORMAT = "#{session_name}\t#{session_created}"
_EXEC_TIMEOUT = 15


async def _require_agent_access(
    request: Request, name: str, user: CurrentUser = Depends(current_user)
) -> None:
    """Only the agent's owner, an admin, or the agent itself may act on it.

    Mirrors routes/agents.py's dependency of the same name, including the
    per-agent local-token fence.
    """
    bound = getattr(request.state, "agent_name", None)
    if bound and bound != name:
        raise HTTPException(status_code=403, detail="forbidden")
    await require_agent_owner_or_admin(request, user, name)


def _resolve_agent(request: Request, name: str) -> dict[str, Any] | None:
    """Return the config agent for *name* (matched by id or name), or None."""
    agents = getattr(request.app.state.config, "agents", [])
    for agent in agents:
        if agent.get("id") == name or agent.get("name") == name:
            return agent
    return None


def _shortcuts_for(agent: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the framework shortcuts list for *agent*, or []."""
    from tinyagentos.frameworks import FRAMEWORKS

    framework = FRAMEWORKS.get(agent.get("framework", ""), {})
    return framework.get("shortcuts", [])


def _container(name: str) -> str:
    return f"taos-agent-{name}"


def _not_found(what: str) -> JSONResponse:
    return JSONResponse({"error": f"{what} not found"}, status_code=404)


async def _list_sessions(name: str) -> list[dict[str, Any]]:
    """This agent's live terminal sessions from ``tmux list-sessions``."""
    code, output = await exec_in_container(
        _container(name),
        ["tmux", "list-sessions", "-F", _LIST_FORMAT],
        timeout=_EXEC_TIMEOUT,
    )
    if code != 0:
        # No tmux server running (no sessions yet) or no tmux in the image:
        # either way there are no persistent terminals to list.
        logger.debug("terminals list for %s: tmux exited %d: %s", name, code, output)
        return []
    sessions: list[dict[str, Any]] = []
    for line in output.splitlines():
        session_name, sep, created = line.partition("\t")
        if not sep:
            continue
        parsed = parse_terminal_session(name, session_name)
        if parsed is None:
            continue
        kind, _idx = parsed
        try:
            started_at = datetime.fromtimestamp(
                int(created), tz=timezone.utc
            ).isoformat()
        except (ValueError, OverflowError):
            continue
        sessions.append(
            {"name": session_name, "kind": kind, "started_at": started_at}
        )
    return sessions


async def _kill_session(name: str, session: str) -> bool:
    """Kill *session* in the agent's container; False when it does not exist."""
    code, output = await exec_in_container(
        _container(name),
        ["tmux", "kill-session", "-t", session],
        timeout=_EXEC_TIMEOUT,
    )
    if code != 0:
        logger.debug("terminals kill %s for %s: %s", session, name, output)
        return False
    return True


@router.get(
    "/api/agents/{name}/terminals", dependencies=[Depends(_require_agent_access)]
)
async def list_agent_terminals(request: Request, name: str):
    """List the agent's active persistent terminal sessions (name, kind, started_at)."""
    if _resolve_agent(request, name) is None:
        return _not_found(f"Agent '{name}'")
    return {"agent": name, "terminals": await _list_sessions(name)}


@router.delete(
    "/api/agents/{name}/terminals/{session}",
    dependencies=[Depends(_require_agent_access)],
)
async def kill_agent_terminal(request: Request, name: str, session: str):
    """Kill one persistent terminal session (tmux kill-session)."""
    if _resolve_agent(request, name) is None:
        return _not_found(f"Agent '{name}'")
    if parse_terminal_session(name, session) is None:
        return _not_found(f"Terminal session '{session}'")
    if not await _kill_session(name, session):
        return _not_found(f"Terminal session '{session}'")
    return {"agent": name, "name": session, "killed": True}


@router.post(
    "/api/agents/{name}/terminals/{session}/restart",
    dependencies=[Depends(_require_agent_access)],
)
async def restart_agent_terminal(request: Request, name: str, session: str):
    """Restart a terminal session: kill it and start a fresh one for the same shortcut."""
    agent = _resolve_agent(request, name)
    if agent is None:
        return _not_found(f"Agent '{name}'")
    parsed = parse_terminal_session(name, session)
    if parsed is None:
        return _not_found(f"Terminal session '{session}'")
    kind, idx = parsed
    shortcuts = _shortcuts_for(agent)
    if idx >= len(shortcuts) or shortcuts[idx].get("kind") != kind:
        return _not_found(f"Terminal session '{session}'")
    if not await _kill_session(name, session):
        return _not_found(f"Terminal session '{session}'")
    if kind == "tui":
        inner_cmd: list[str] | None = [
            "bash",
            "-lc",
            shortcuts[idx].get("command", ""),
        ]
    else:
        inner_cmd = None
    code, output = await exec_in_container(
        _container(name),
        tmux_session_cmd(session, inner_cmd, detached=True),
        timeout=_EXEC_TIMEOUT,
    )
    if code != 0:
        logger.warning(
            "terminals restart %s for %s: create failed: %s", session, name, output
        )
        return JSONResponse(
            {"error": f"Failed to restart terminal session '{session}'"},
            status_code=502,
        )
    return {"agent": name, "name": session, "kind": kind, "restarted": True}
