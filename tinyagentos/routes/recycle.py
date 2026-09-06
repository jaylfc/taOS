"""Recycle-bin API routes for agent containers.

Exposes list, restore, and purge operations against each agent container's
trash-cli XDG recycle bin at /var/recycle-bin/ (the default trash-cli
TRASH_DIR when run as root inside a container).

Container interactions go via exec_in_container so the same code works for
both LXC and Docker backends transparently.
"""
from __future__ import annotations

import base64
import logging
import re
from pathlib import PurePosixPath

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.agent_db import find_agent
from tinyagentos.containers import exec_in_container

logger = logging.getLogger(__name__)
router = APIRouter()

# trash-list output format: YYYY-MM-DD HH:MM:SS <original-path>
_TRASH_LIST_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) (.+)$")


def _encode_id(original_path: str) -> str:
    """Encode a path to a base64url token (no padding) for use as an item id."""
    return base64.urlsafe_b64encode(original_path.encode()).decode().rstrip("=")


def _decode_id(token: str) -> str:
    """Decode a base64url id back to the original path."""
    pad = 4 - (len(token) % 4)
    if pad == 4:
        pad = 0
    return base64.urlsafe_b64decode((token + "=" * pad).encode()).decode()


def _shell_quote(s: str) -> str:
    """Single-quote a string for safe use in a bash -c command."""
    return "'" + s.replace("'", "'\\''") + "'"


async def _list_for_agent(container: str) -> tuple[str, list[dict]]:
    """Return (status, items) for a container's recycle bin.

    status is 'ok' or 'container_offline'.
    Calls trash-list which prints lines of: YYYY-MM-DD HH:MM:SS <path>
    """
    try:
        rc, out = await exec_in_container(container, ["trash-list"], timeout=10)
    except Exception as exc:
        logger.info("recycle list: container %s unreachable: %s", container, exc)
        return "container_offline", []

    if rc != 0:
        return "container_offline", []

    items: list[dict] = []
    for line in out.splitlines():
        m = _TRASH_LIST_RE.match(line.strip())
        if not m:
            continue
        date, time_, path = m.groups()
        items.append({
            "id": _encode_id(path),
            "original_path": path,
            "deleted_at": f"{date}T{time_}Z",
            "size_bytes": None,
        })
    return "ok", items


@router.get("/api/agents/{name}/recycle")
async def list_agent_recycle(request: Request, name: str):
    """List trashed items for one agent's container."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if agent is None:
        return JSONResponse({"error": f"Agent {name!r} not found"}, status_code=404)
    container = f"taos-agent-{name}"
    status, items = await _list_for_agent(container)
    return {"agent_name": name, "items": items, "status": status}


@router.get("/api/recycle")
async def list_all_recycle(request: Request):
    """Aggregated recycle bin view across all configured agents, newest first."""
    config = request.app.state.config
    all_items: list[dict] = []
    for agent in config.agents or []:
        agent_name = agent.get("name")
        if not agent_name:
            continue
        container = f"taos-agent-{agent_name}"
        _status, items = await _list_for_agent(container)
        for item in items:
            all_items.append({"agent_name": agent_name, **item})
    all_items.sort(key=lambda x: x.get("deleted_at", ""), reverse=True)
    return {"items": all_items}


class RestoreBody(BaseModel):
    id: str | None = None
    original_path: str | None = None


@router.post("/api/agents/{name}/recycle/restore")
async def restore_item(request: Request, name: str, body: RestoreBody):
    """Restore a single trashed item back to its original path.

    Accepts either 'id' (base64url of original_path) or 'original_path' directly.

    trash-restore is interactive by default: it presents a numbered list and
    reads a choice from stdin. We send "0\\n" to always pick the first match,
    which is correct when restoring a specific path (trash-restore filters by
    the path argument so only matching entries are numbered).
    """
    config = request.app.state.config
    agent = find_agent(config, name)
    if agent is None:
        return JSONResponse({"error": f"Agent {name!r} not found"}, status_code=404)

    # Resolve path from id or direct value
    path = body.original_path
    if path is None and body.id:
        try:
            path = _decode_id(body.id)
        except Exception:
            return JSONResponse({"error": "invalid id"}, status_code=400)
    if not path:
        return JSONResponse({"error": "id or original_path required"}, status_code=400)

    # Path safety: must be absolute, no null bytes
    if "\x00" in path or not PurePosixPath(path).is_absolute():
        return JSONResponse({"error": "invalid path"}, status_code=400)

    container = f"taos-agent-{name}"
    # Send "0\n" as stdin input so trash-restore picks the first (and only)
    # matching entry without blocking on interactive input.
    bash_cmd = f"printf '0\\n' | trash-restore -- {_shell_quote(path)}"
    try:
        rc, out = await exec_in_container(
            container, ["bash", "-c", bash_cmd], timeout=15
        )
    except Exception as exc:
        logger.info("recycle restore: container %s unreachable: %s", container, exc)
        return JSONResponse({"error": "container offline"}, status_code=409)

    if rc != 0:
        out_lower = out.lower()
        if "no such file" in out_lower or "not found" in out_lower or "no files" in out_lower:
            return JSONResponse({"error": "item not found"}, status_code=404)
        return JSONResponse(
            {"error": f"restore failed: {out.strip()[:200]}"}, status_code=500
        )
    return {"status": "restored", "original_path": path}


@router.delete("/api/agents/{name}/recycle/{item_id}")
async def purge_item(request: Request, name: str, item_id: str):
    """Permanently purge a single trashed item by id."""
    config = request.app.state.config
    agent = find_agent(config, name)
    if agent is None:
        return JSONResponse({"error": f"Agent {name!r} not found"}, status_code=404)

    try:
        path = _decode_id(item_id)
    except Exception:
        return JSONResponse({"error": "invalid id"}, status_code=400)

    if "\x00" in path or not PurePosixPath(path).is_absolute():
        return JSONResponse({"error": "invalid path"}, status_code=400)

    container = f"taos-agent-{name}"
    bash_cmd = f"trash-rm -- {_shell_quote(path)}"
    try:
        rc, out = await exec_in_container(
            container, ["bash", "-c", bash_cmd], timeout=10
        )
    except Exception as exc:
        logger.info("recycle purge: container %s unreachable: %s", container, exc)
        return JSONResponse({"error": "container offline"}, status_code=409)

    if rc != 0:
        return JSONResponse(
            {"error": f"purge failed: {out.strip()[:200]}"}, status_code=500
        )
    return {"status": "purged", "id": item_id}
