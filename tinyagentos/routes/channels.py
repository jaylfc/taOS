from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tinyagentos.channels import CHANNEL_TYPES

logger = logging.getLogger(__name__)

router = APIRouter()


class AddChannelRequest(BaseModel):
    agent_name: str
    type: str
    config: dict = {}


class ToggleRequest(BaseModel):
    enabled: bool


@router.get("/api/channels")
async def api_list_channels(request: Request):
    store = request.app.state.channels
    channels = await store.list_all()
    return channels


@router.get("/api/channels/types")
async def api_channel_types():
    return CHANNEL_TYPES


@router.get("/api/channels/agent/{agent_name}")
async def api_agent_channels(request: Request, agent_name: str):
    store = request.app.state.channels
    channels = await store.list_for_agent(agent_name)
    return channels


@router.post("/api/channels")
async def api_add_channel(request: Request, body: AddChannelRequest):
    if body.type not in CHANNEL_TYPES:
        return JSONResponse({"error": f"Unknown channel type: {body.type}"}, status_code=400)
    store = request.app.state.channels
    channel_id = await store.add(body.agent_name, body.type, body.config)
    return {"id": channel_id, "status": "added"}


@router.delete("/api/channels/{agent_name}/{channel_type}")
async def api_remove_channel(request: Request, agent_name: str, channel_type: str):
    store = request.app.state.channels
    removed = await store.remove(agent_name, channel_type)
    if not removed:
        return JSONResponse({"error": "Channel not found"}, status_code=404)
    return {"status": "removed"}


@router.post("/api/channels/{channel_id}/toggle")
async def api_toggle_channel(request: Request, channel_id: int, body: ToggleRequest):
    store = request.app.state.channels
    await store.toggle(channel_id, body.enabled)
    return {"status": "toggled", "enabled": body.enabled}
