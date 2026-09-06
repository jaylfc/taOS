from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()


class SendMessageRequest(BaseModel):
    from_agent: str
    message: str
    tool_calls: list | None = None
    tool_results: list | None = None
    reasoning: str = ""
    depth: int = 2
    metadata: dict | None = None


# ---------------------------------------------------------------------------
# Messages view
# ---------------------------------------------------------------------------


@router.get("/api/agents/{name}/workspace/messages/contacts")
async def api_agent_contacts(request: Request, name: str):
    """List agents this agent has communicated with + unread counts."""
    msg_store = request.app.state.agent_messages
    contacts = await msg_store.get_contacts(name)
    return contacts


@router.get("/api/agents/{name}/messages")
async def api_agent_messages(request: Request, name: str, limit: int = 50, depth: int = 2):
    msg_store = request.app.state.agent_messages
    messages = await msg_store.get_messages(name, limit=limit, depth=depth)
    return messages


@router.post("/api/agents/{name}/messages")
async def api_send_message(request: Request, name: str, body: SendMessageRequest):
    msg_store = request.app.state.agent_messages
    msg_id = await msg_store.send(
        from_agent=body.from_agent,
        to_agent=name,
        message=body.message,
        tool_calls=body.tool_calls,
        tool_results=body.tool_results,
        reasoning=body.reasoning,
        depth=body.depth,
        metadata=body.metadata,
    )
    return {"id": msg_id, "status": "sent"}


# ---------------------------------------------------------------------------
# Files view — routes live in tinyagentos/routes/agent_workspace.py.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# LLM Usage
# ---------------------------------------------------------------------------


@router.get("/api/agents/{name}/workspace/usage")
async def api_agent_usage(request: Request, name: str):
    """Get LLM usage stats for an agent's virtual key."""
    proxy = getattr(request.app.state, "llm_proxy", None)
    if not proxy or not proxy.is_running():
        return {"available": False, "message": "LLM proxy not running"}
    # Look up the agent's key alias
    config = request.app.state.config
    agent = None
    for a in config.agents:
        if a.get("name") == name:
            agent = a
            break
    if not agent:
        return JSONResponse({"error": f"Agent '{name}' not found"}, status_code=404)
    llm_key = agent.get("llm_key")
    if not llm_key:
        return {"available": False, "message": "No LLM key assigned to this agent"}
    usage = await proxy.get_key_usage(llm_key)
    if usage is None:
        return {"available": False, "message": "Could not fetch usage data"}
    return {"available": True, "usage": usage}


# Keep legacy files endpoint for backward compatibility
@router.get("/api/agents/{name}/files")
async def api_agent_files(request: Request, name: str):
    """List files in agent's workspace directory (legacy endpoint)."""
    data_dir = request.app.state.config_path.parent
    workspace_dir = data_dir / "workspaces" / name
    if not workspace_dir.exists():
        return []
    files = []
    for f in sorted(workspace_dir.iterdir()):
        if f.is_file():
            files.append({
                "name": f.name,
                "size": f.stat().st_size,
                "modified": f.stat().st_mtime,
            })
    return files
