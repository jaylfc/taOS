from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.post("/api/chat/channels")
async def create_channel(request: Request):
    body = await request.json()
    ch_store = request.app.state.chat_channels
    channel = await ch_store.create_channel(
        name=body["name"],
        type=body.get("type", "topic"),
        created_by=body.get("created_by", "user"),
        members=body.get("members"),
        description=body.get("description", ""),
        topic=body.get("topic", ""),
        project_id=body.get("project_id", ""),
    )
    return channel


@router.put("/api/chat/channels/{channel_id}")
async def update_channel(request: Request, channel_id: str):
    body = await request.json()
    ch_store = request.app.state.chat_channels
    channel = await ch_store.get_channel(channel_id)
    if not channel:
        return JSONResponse({"error": "Channel not found"}, status_code=404)
    await ch_store.update_channel(
        channel_id,
        name=body.get("name"),
        description=body.get("description"),
        topic=body.get("topic"),
    )
    return {"status": "updated"}


@router.delete("/api/chat/channels/{channel_id}")
async def delete_channel(request: Request, channel_id: str):
    ch_store = request.app.state.chat_channels
    deleted = await ch_store.delete_channel(channel_id)
    if not deleted:
        return JSONResponse({"error": "Channel not found"}, status_code=404)
    return {"status": "deleted"}


@router.delete("/api/chat/channels/{channel_id}/members/{member_id}")
async def remove_channel_member(request: Request, channel_id: str, member_id: str):
    ch_store = request.app.state.chat_channels
    await ch_store.remove_member(channel_id, member_id)
    return {"status": "removed"}


@router.patch("/api/chat/channels/{channel_id}")
async def update_channel_settings(channel_id: str, body: dict, request: Request):
    """Update channel settings. Body may include: response_mode, max_hops,
    cooldown_seconds, topic, name. Each is optional; only provided keys are
    applied. Returns 400 on validation failure."""
    state = request.app.state
    chs = state.chat_channels
    ch = await chs.get_channel(channel_id)
    if ch is None:
        return JSONResponse({"error": "channel not found"}, status_code=404)
    try:
        if "response_mode" in body:
            await chs.set_response_mode(channel_id, body["response_mode"])
        if "max_hops" in body:
            await chs.set_max_hops(channel_id, int(body["max_hops"]))
        if "cooldown_seconds" in body:
            await chs.set_cooldown_seconds(channel_id, int(body["cooldown_seconds"]))
        if "ephemeral_ttl_seconds" in body:
            raw = body["ephemeral_ttl_seconds"]
            ttl: int | None = None if raw is None else int(raw)
            await chs.set_ephemeral_ttl(channel_id, ttl)
        if "topic" in body:
            topic = str(body["topic"])
            if len(topic) > 500:
                return JSONResponse({"error": "topic must be <= 500 chars"}, status_code=400)
            await chs.update_channel(channel_id, topic=topic)
        if "name" in body:
            name = str(body["name"]).strip()
            if not name or len(name) > 100:
                return JSONResponse({"error": "name must be 1..100 chars"}, status_code=400)
            await chs.update_channel(channel_id, name=name)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return JSONResponse({"ok": True}, status_code=200)


@router.post("/api/chat/channels/{channel_id}/members")
async def modify_channel_members(channel_id: str, body: dict, request: Request):
    """Add or remove a member. Body: {"action": "add" | "remove", "slug": "..."}."""
    action = (body.get("action") or "").lower()
    slug = (body.get("slug") or "").lstrip("@")
    if action not in ("add", "remove") or not slug:
        return JSONResponse({"error": "action must be add|remove, slug required"}, status_code=400)
    state = request.app.state
    chs = state.chat_channels
    ch = await chs.get_channel(channel_id)
    if ch is None:
        return JSONResponse({"error": "channel not found"}, status_code=404)
    if action == "add":
        known = {a.get("name") for a in getattr(state.config, "agents", []) or []}
        if slug != "user" and slug not in known:
            return JSONResponse({"error": f"unknown agent: {slug}"}, status_code=400)
        await chs.add_member(channel_id, slug)
    else:
        await chs.remove_member(channel_id, slug)
    return JSONResponse({"ok": True}, status_code=200)


@router.post("/api/chat/channels/{channel_id}/muted")
async def modify_channel_muted(channel_id: str, body: dict, request: Request):
    """Add or remove an agent from the channel's muted list.
    Body: {"action": "add" | "remove", "slug": "..."}."""
    action = (body.get("action") or "").lower()
    slug = (body.get("slug") or "").lstrip("@")
    if action not in ("add", "remove") or not slug:
        return JSONResponse({"error": "action must be add|remove, slug required"}, status_code=400)
    state = request.app.state
    chs = state.chat_channels
    ch = await chs.get_channel(channel_id)
    if ch is None:
        return JSONResponse({"error": "channel not found"}, status_code=404)
    if action == "add":
        await chs.mute_agent(channel_id, slug)
    else:
        await chs.unmute_agent(channel_id, slug)
    return JSONResponse({"ok": True}, status_code=200)
