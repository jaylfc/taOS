from __future__ import annotations

import asyncio
import json
import logging
import mimetypes
import os
import secrets
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response

from tinyagentos.chat.reactions import maybe_trigger_semantic

router = APIRouter()
logger = logging.getLogger(__name__)

_background_tasks: set[asyncio.Task] = set()


def _spawn_background(coro) -> asyncio.Task:
    """Schedule a fire-and-forget coroutine, retaining a reference so it
    cannot be GC'd before completion (RUF006)."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return task


async def _capture_user_memory(
    user_memory,
    *,
    content: str,
    title: str,
    collection: str,
    metadata: dict,
    setting_key: str,
    user_id: str = "user",
) -> None:
    """Fire-and-forget user memory capture. Never raises."""
    if not user_memory or not content:
        return
    try:
        settings = await user_memory.get_settings(user_id)
        if not settings.get(setting_key):
            return
        await user_memory.save_chunk(
            user_id,
            content=content,
            title=title,
            collection=collection,
            metadata=metadata,
        )
    except Exception as e:  # pragma: no cover - capture is best-effort
        logger.debug(f"user memory capture failed: {e}")


async def _beads_on_chat_message(app, channel: dict, message: dict) -> None:
    """Best-effort hand-off to the Beads bridge. Never raises."""
    bridge = getattr(app.state, "beads_bridge", None)
    if bridge is None:
        return
    project_id = channel.get("project_id")
    if not project_id:
        return
    try:
        await bridge.on_chat_message(project_id, channel["id"], message)
    except Exception:
        logger.warning("beads on_chat_message failed", exc_info=True)


@router.get("/api/docs/chat-guide")
async def get_chat_guide():
    from pathlib import Path as _Path
    guide = _Path(__file__).resolve().parent.parent.parent / "docs" / "chat-guide.md"
    if not guide.exists():
        return JSONResponse({"error": "guide not found"}, status_code=404)
    return JSONResponse({"markdown": guide.read_text(encoding="utf-8")})


@router.websocket("/ws/chat")
async def chat_ws(websocket: WebSocket):
    await websocket.accept()
    user_id = "user"  # For now, single user. Auth integration in future.
    hub = websocket.app.state.chat_hub
    hub.connect(websocket, user_id)
    try:
        while True:
            data = json.loads(await websocket.receive_text())
            msg_type = data.get("type")

            if msg_type == "join":
                hub.join(websocket, data["channel_id"])

            elif msg_type == "leave":
                hub.leave(websocket, data["channel_id"])

            elif msg_type == "message":
                msg_store = websocket.app.state.chat_messages
                ch_store = websocket.app.state.chat_channels
                _ws_channel = await ch_store.get_channel(data["channel_id"])
                _ws_ttl = None
                if _ws_channel and _ws_channel.get("settings"):
                    _ws_ttl = _ws_channel["settings"].get("ephemeral_ttl_seconds")
                import time as _time
                _ws_expires_at = (_time.time() + _ws_ttl) if isinstance(_ws_ttl, (int, float)) and _ws_ttl > 0 else None
                message = await msg_store.send_message(
                    channel_id=data["channel_id"],
                    author_id=user_id,
                    author_type="user",
                    content=data.get("content", ""),
                    content_type=data.get("content_type", "text"),
                    thread_id=data.get("thread_id"),
                    embeds=data.get("embeds"),
                    components=data.get("components"),
                    attachments=data.get("attachments"),
                    content_blocks=data.get("content_blocks"),
                    metadata=data.get("metadata"),
                    expires_at=_ws_expires_at,
                )
                await ch_store.update_last_message_at(data["channel_id"])
                await hub.broadcast(data["channel_id"], {"type": "message", "seq": hub.next_seq(), **message})

                router_svc = getattr(websocket.app.state, "agent_chat_router", None)
                if _ws_channel is not None:
                    if router_svc is not None:
                        router_svc.dispatch(message, _ws_channel)
                    _spawn_background(
                        _beads_on_chat_message(websocket.app, _ws_channel, message)
                    )

                # Capture user message into user memory (async, non-blocking)
                user_memory = getattr(websocket.app.state, "user_memory", None)
                if user_memory:
                    asyncio.create_task(_capture_user_memory(
                        user_memory,
                        content=data.get("content", ""),
                        title=f"Message in {data['channel_id']}",
                        collection="conversations",
                        metadata={
                            "channel_id": data["channel_id"],
                            "message_id": message.get("id"),
                            "timestamp": message.get("created_at"),
                        },
                        setting_key="capture_conversations",
                        user_id=user_id,
                    ))

            elif msg_type == "typing":
                hub.set_typing(data["channel_id"], user_id)
                await hub.broadcast(data["channel_id"], {
                    "type": "typing",
                    "seq": hub.next_seq(),
                    "channel_id": data["channel_id"],
                    "user_id": user_id,
                    "user_type": "user",
                })

            elif msg_type == "reaction":
                msg_store = websocket.app.state.chat_messages
                if data.get("action") == "remove":
                    await msg_store.remove_reaction(data["message_id"], data["emoji"], user_id)
                else:
                    await msg_store.add_reaction(data["message_id"], data["emoji"], user_id)
                msg = await msg_store.get_message(data["message_id"])
                if msg:
                    await hub.broadcast(msg["channel_id"], {
                        "type": "reaction_update",
                        "seq": hub.next_seq(),
                        "message_id": data["message_id"],
                        "reactions": msg["reactions"],
                    })

            elif msg_type == "edit":
                msg_store = websocket.app.state.chat_messages
                await msg_store.edit_message(data["message_id"], data["content"])
                msg = await msg_store.get_message(data["message_id"])
                if msg:
                    await hub.broadcast(msg["channel_id"], {
                        "type": "message_edit",
                        "seq": hub.next_seq(),
                        "message_id": data["message_id"],
                        "content": data["content"],
                        "edited_at": msg["edited_at"],
                    })

            elif msg_type == "delete":
                msg_store = websocket.app.state.chat_messages
                msg = await msg_store.get_message(data["message_id"])
                if msg:
                    channel_id = msg["channel_id"]
                    await msg_store.delete_message(data["message_id"])
                    await hub.broadcast(channel_id, {
                        "type": "message_delete",
                        "seq": hub.next_seq(),
                        "message_id": data["message_id"],
                        "channel_id": channel_id,
                    })

            elif msg_type == "mark_read":
                ch_store = websocket.app.state.chat_channels
                await ch_store.update_read_position(user_id, data["channel_id"], data["message_id"])

            elif msg_type == "component_action":
                await hub.broadcast(data.get("channel_id", ""), {
                    "type": "component_action",
                    "seq": hub.next_seq(),
                    "message_id": data["message_id"],
                    "action": data["action"],
                    "value": data.get("value"),
                    "user_id": user_id,
                })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"Chat WS error: {e}")
    finally:
        hub.disconnect(websocket, user_id)


@router.post("/api/chat/messages")
async def post_message(request: Request):
    """Send a message via HTTP (used by agents and the agent-bridge)."""
    body = await request.json()
    msg_store = request.app.state.chat_messages
    ch_store = request.app.state.chat_channels
    hub = request.app.state.chat_hub

    channel_id = body["channel_id"]
    content = body.get("content") or ""

    if content.startswith("/help"):
        from tinyagentos.chat.help import handle_help
        args = content[len("/help"):].lstrip()
        system_text = handle_help(args)
        sys_msg = await msg_store.send_message(
            channel_id=channel_id,
            author_id="system",
            author_type="system",
            content=system_text,
            content_type="text",
            state="complete",
            metadata=None,
        )
        await ch_store.update_last_message_at(channel_id)
        await hub.broadcast(
            channel_id,
            {"type": "message", "seq": hub.next_seq(), **sys_msg},
        )
        return JSONResponse(
            {"ok": True, "handled": "help", "system_message": sys_msg},
            status_code=200,
        )

    # Guardrail: in a non-DM channel, a / message must address at least one
    # agent explicitly (@<slug> or @all). Otherwise a framework slash command
    # would broadcast to every agent in the channel, producing N different
    # /help outputs and (in some frameworks) triggering destructive side effects
    # like /clear on unaddressed agents.
    stripped = content.lstrip()
    if stripped.startswith("/"):
        channel = await ch_store.get_channel(channel_id)
        _is_a2a = ((channel or {}).get("settings") or {}).get("kind") == "a2a"
        if channel and channel.get("type") != "dm" and not _is_a2a:
            from tinyagentos.chat.mentions import parse_mentions
            members = list(channel.get("members") or [])
            mentions = parse_mentions(content, members)
            if not mentions.explicit and not mentions.all:
                return JSONResponse(
                    {"error": "slash commands in group channels must address an agent: use @<agent> /<cmd> or @all /<cmd>"},
                    status_code=400,
                )

    attachments = (body or {}).get("attachments") or []
    if not isinstance(attachments, list):
        return JSONResponse({"error": "attachments must be a list"}, status_code=400)
    if len(attachments) > 10:
        return JSONResponse({"error": "max 10 attachments per message"}, status_code=400)
    data_dir = Path(getattr(request.app.state, "data_dir",
                            Path(os.environ.get("TAOS_DATA_DIR", "./data"))))
    chat_files = data_dir / "chat-files"
    for att in attachments:
        if not isinstance(att, dict):
            return JSONResponse({"error": "each attachment must be a dict"}, status_code=400)
        url = att.get("url", "")
        if not url.startswith("/api/chat/files/"):
            return JSONResponse(
                {"error": "attachment url must be served from /api/chat/files/"},
                status_code=400,
            )
        stored_name = url.rsplit("/", 1)[-1]
        if not (chat_files / stored_name).exists():
            return JSONResponse(
                {"error": f"attachment file not found: {stored_name}"},
                status_code=400,
            )

    _http_channel = await ch_store.get_channel(channel_id)
    _http_ttl = None
    if _http_channel and _http_channel.get("settings"):
        _http_ttl = _http_channel["settings"].get("ephemeral_ttl_seconds")
    import time as _time
    _http_expires_at = (_time.time() + _http_ttl) if isinstance(_http_ttl, (int, float)) and _http_ttl > 0 else None

    message = await msg_store.send_message(
        channel_id=channel_id,
        author_id=body["author_id"],
        author_type=body.get("author_type", "agent"),
        content=content,
        content_type=body.get("content_type", "text"),
        thread_id=body.get("thread_id"),
        embeds=body.get("embeds"),
        components=body.get("components"),
        attachments=attachments,
        content_blocks=body.get("content_blocks"),
        metadata=body.get("metadata"),
        state=body.get("state", "complete"),
        expires_at=_http_expires_at,
    )
    await ch_store.update_last_message_at(channel_id)
    await hub.broadcast(channel_id, {"type": "message", "seq": hub.next_seq(), **message})

    # Capture user messages into user memory (skip agent messages)
    if body.get("author_type", "agent") == "user":
        user_memory = getattr(request.app.state, "user_memory", None)
        if user_memory:
            asyncio.create_task(_capture_user_memory(
                user_memory,
                content=content,
                title=f"Message in {channel_id}",
                collection="conversations",
                metadata={
                    "channel_id": channel_id,
                    "message_id": message.get("id"),
                    "timestamp": message.get("created_at"),
                },
                setting_key="capture_conversations",
            ))

    # Auto-archive every message for the zero-loss layer
    archive = getattr(request.app.state, "archive", None)
    if archive:
        try:
            await archive.record(
                "conversation",
                {
                    "content": content,
                    "channel_id": channel_id,
                    "message_id": message.get("id"),
                    "author_id": body["author_id"],
                    "author_type": body.get("author_type", "agent"),
                },
                agent_name=body["author_id"] if body.get("author_type") == "agent" else None,
                summary=content[:100],
            )
        except Exception:
            pass  # Never block chat for archive failures

    router_svc = getattr(request.app.state, "agent_chat_router", None)
    if _http_channel is not None:
        if router_svc is not None:
            router_svc.dispatch(message, _http_channel)
        _spawn_background(
            _beads_on_chat_message(request.app, _http_channel, message)
        )

    return message


@router.post("/api/chat/messages/{message_id}/delta")
async def post_message_delta(request: Request, message_id: str):
    """Stream a token delta for an agent response. Used by framework adapters."""
    body = await request.json()
    hub = request.app.state.chat_hub
    channel_id = body.get("channel_id", "")
    await hub.broadcast(channel_id, {
        "type": "message_delta",
        "seq": hub.next_seq(),
        "message_id": message_id,
        "channel_id": channel_id,
        "delta": body.get("delta", ""),
    })
    return {"status": "sent"}


@router.post("/api/chat/messages/{message_id}/state")
async def update_message_state(request: Request, message_id: str):
    """Update message state (pending/streaming/complete/error)."""
    body = await request.json()
    msg_store = request.app.state.chat_messages
    hub = request.app.state.chat_hub
    await msg_store.update_state(message_id, body["state"])
    msg = await msg_store.get_message(message_id)
    if msg:
        await hub.broadcast(msg["channel_id"], {
            "type": "message_state",
            "seq": hub.next_seq(),
            "message_id": message_id,
            "state": body["state"],
        })
    return {"status": "updated"}


# ── Channel CRUD ──────────────────────────────────────────────────────────────

@router.get("/api/chat/channels")
async def list_channels(
    request: Request,
    member: str | None = None,
    archived: bool | None = None,
    project_id: str | None = None,
):
    ch_store = request.app.state.chat_channels
    channels = await ch_store.list_channels(member_id=member, archived=archived, project_id=project_id)
    return {"channels": channels}


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


@router.get("/api/chat/channels/{channel_id}")
async def get_channel(request: Request, channel_id: str):
    ch_store = request.app.state.chat_channels
    channel = await ch_store.get_channel(channel_id)
    if not channel:
        return JSONResponse({"error": "Channel not found"}, status_code=404)
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


@router.get("/api/chat/channels/{channel_id}/messages")
async def get_channel_messages(
    request: Request, channel_id: str, limit: int = 50, before: float | None = None
):
    msg_store = request.app.state.chat_messages
    messages = await msg_store.get_messages(channel_id, limit=limit, before=before)
    return {"messages": messages}


@router.get("/api/chat/messages/{message_id}")
async def get_message_by_id(message_id: str, request: Request):
    store = request.app.state.chat_messages
    msg = await store.get_message(message_id)
    if msg is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(msg)


@router.get("/api/chat/channels/{channel_id}/threads/{parent_id}/messages")
async def get_thread_messages_endpoint(
    channel_id: str, parent_id: str, request: Request, limit: int = 20,
):
    store = request.app.state.chat_messages
    msgs = await store.get_thread_messages(channel_id, parent_id, limit=min(limit, 100))
    return JSONResponse({"messages": msgs})


@router.get("/api/chat/channels/{channel_id}/threads")
async def get_channel_threads_endpoint(channel_id: str, request: Request):
    store = request.app.state.chat_messages
    threads = await store.get_channel_threads(channel_id)
    return JSONResponse({"threads": threads})


@router.get("/api/chat/channels/{channel_id}/pins")
async def get_channel_pins(channel_id: str, request: Request):
    store = request.app.state.chat_messages
    pins = await store.get_pins(channel_id)
    return JSONResponse({"pins": pins})


@router.post("/api/chat/messages/{message_id}/pin")
async def pin_message_endpoint(message_id: str, request: Request):
    msg_store = request.app.state.chat_messages
    msg = await msg_store.get_message(message_id)
    if msg is None or msg.get("deleted_at"):
        return JSONResponse({"error": "message not found"}, status_code=404)
    # Resolve caller id from session cookie
    auth = getattr(request.app.state, "auth", None)
    session_user = None
    if auth is not None:
        token = request.cookies.get("taos_session") or ""
        if token:
            session_user = auth.get_user(token=token)
    pinned_by = f"user:{session_user['id']}" if session_user else "user:unknown"
    try:
        await msg_store.pin_message(msg["channel_id"], message_id, pinned_by=pinned_by)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=409)
    # Clear pin_requested flag if it was set (agent had asked for a pin)
    meta = msg.get("metadata") or {}
    if meta.get("pin_requested"):
        meta.pop("pin_requested", None)
        await msg_store.set_metadata(message_id, meta)
    hub = request.app.state.chat_hub
    await hub.broadcast(msg["channel_id"], {
        "type": "pin", "seq": hub.next_seq(),
        "channel_id": msg["channel_id"], "message_id": message_id, "pinned_by": pinned_by,
    })
    return JSONResponse({"ok": True, "pinned_by": pinned_by})


@router.delete("/api/chat/messages/{message_id}/pin")
async def unpin_message_endpoint(message_id: str, request: Request):
    msg_store = request.app.state.chat_messages
    msg = await msg_store.get_message(message_id)
    if msg is None:
        return JSONResponse({"error": "message not found"}, status_code=404)
    ok = await msg_store.unpin_message(msg["channel_id"], message_id)
    if not ok:
        return JSONResponse({"error": "message not pinned"}, status_code=404)
    hub = request.app.state.chat_hub
    await hub.broadcast(msg["channel_id"], {
        "type": "unpin", "seq": hub.next_seq(),
        "channel_id": msg["channel_id"], "message_id": message_id,
    })
    return Response(status_code=204)


@router.patch("/api/chat/messages/{message_id}")
async def edit_message_endpoint(message_id: str, request: Request):
    body = await request.json()
    allowed = {"content"}
    if set(body.keys()) - allowed:
        return JSONResponse(
            {"error": "only 'content' may be edited"}, status_code=400,
        )
    if "content" not in body or not isinstance(body["content"], str):
        return JSONResponse({"error": "content required"}, status_code=400)
    msg_store = request.app.state.chat_messages
    msg = await msg_store.get_message(message_id)
    if msg is None or msg.get("deleted_at"):
        return JSONResponse({"error": "message not found"}, status_code=404)
    auth = getattr(request.app.state, "auth", None)
    session_user = None
    if auth is not None:
        token = request.cookies.get("taos_session") or ""
        session_user = auth.get_user(token=token)
    caller_id = session_user["id"] if session_user else None
    if msg["author_id"] != caller_id:
        return JSONResponse({"error": "not the author"}, status_code=403)
    await msg_store.edit_message(message_id, body["content"])
    updated = await msg_store.get_message(message_id)
    hub = request.app.state.chat_hub
    await hub.broadcast(msg["channel_id"], {
        "type": "message_edit", "seq": hub.next_seq(),
        "message_id": message_id,
        "content": updated["content"],
        "edited_at": updated["edited_at"],
    })
    return JSONResponse(updated)


@router.delete("/api/chat/messages/{message_id}")
async def delete_message_endpoint(message_id: str, request: Request):
    msg_store = request.app.state.chat_messages
    msg = await msg_store.get_message(message_id)
    if msg is None:
        return JSONResponse({"error": "message not found"}, status_code=404)
    auth = getattr(request.app.state, "auth", None)
    session_user = None
    if auth is not None:
        token = request.cookies.get("taos_session") or ""
        session_user = auth.get_user(token=token)
    caller_id = session_user["id"] if session_user else None
    if msg["author_id"] != caller_id:
        return JSONResponse({"error": "not the author"}, status_code=403)
    await msg_store.soft_delete_message(message_id)
    hub = request.app.state.chat_hub
    deleted = await msg_store.get_message(message_id)
    await hub.broadcast(msg["channel_id"], {
        "type": "message_delete", "seq": hub.next_seq(),
        "channel_id": msg["channel_id"], "message_id": message_id,
        "deleted_at": (deleted or {}).get("deleted_at"),
    })
    return Response(status_code=204)


@router.delete("/api/chat/channels/{channel_id}/members/{member_id}")
async def remove_channel_member(request: Request, channel_id: str, member_id: str):
    ch_store = request.app.state.chat_channels
    await ch_store.remove_member(channel_id, member_id)
    return {"status": "removed"}


# ── Admin endpoints (UI-driven channel control-plane) ─────────────────────────

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


# ── File upload / serve ───────────────────────────────────────────────────────

_MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024  # 100 MB


def _resolve_workspace_path(data_dir: Path, source: str, slug: str | None, vfs_path: str) -> Path:
    """Resolve a VFS path like '/workspaces/user/foo.md' to an on-disk
    absolute path under data_dir/agent-workspaces/{slug-or-user}.
    Raises ValueError on traversal or bad shape.
    """
    if not vfs_path.startswith("/workspaces/"):
        raise ValueError("path must start with /workspaces/")
    parts = vfs_path.split("/", 3)  # ['', 'workspaces', '<slug>', 'rest...']
    if len(parts) < 3 or not parts[2]:
        raise ValueError("path missing slug")
    owner = parts[2]
    if source == "agent-workspace":
        if not slug or slug != owner:
            raise ValueError("slug must match path owner for agent-workspace")
    if source == "workspace":
        if owner != "user":
            raise ValueError("workspace source requires /workspaces/user/...")
    rel = parts[3] if len(parts) > 3 else ""
    root = (data_dir / "agent-workspaces" / owner).resolve()
    target = (root / rel).resolve()
    # Traversal check: target must be inside root.
    if not str(target).startswith(str(root) + os.sep) and target != root:
        raise ValueError("path traversal rejected")
    if not target.exists() or target.is_dir():
        raise ValueError("file not found")
    return target


@router.post("/api/chat/attachments/from-path")
async def attachment_from_path(body: dict, request: Request):
    """Server-side reference to a file in a workspace. Copies into
    chat-files/ and returns the attachment record."""
    vfs_path = (body or {}).get("path")
    source = (body or {}).get("source")
    slug = (body or {}).get("slug")
    if not vfs_path or source not in ("workspace", "agent-workspace"):
        return JSONResponse(
            {"error": "path and source in {workspace,agent-workspace} required"},
            status_code=400,
        )
    data_dir = request.app.state.config_path.parent
    try:
        src = _resolve_workspace_path(data_dir, source, slug, vfs_path)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    if src.stat().st_size > _MAX_ATTACHMENT_BYTES:
        return JSONResponse({"error": "file too large (100 MB max)"}, status_code=413)
    chat_files = data_dir / "chat-files"
    chat_files.mkdir(parents=True, exist_ok=True)
    stored_name = f"{secrets.token_hex(8)}-{src.name}"
    dest = chat_files / stored_name
    shutil.copy2(src, dest)
    mime, _ = mimetypes.guess_type(src.name)
    return JSONResponse({
        "filename": src.name,
        "mime_type": mime or "application/octet-stream",
        "size": src.stat().st_size,
        "url": f"/api/chat/files/{stored_name}",
        "source": source,
    }, status_code=200)


@router.post("/api/chat/upload")
async def upload_file(request: Request, file: UploadFile = File(...), channel_id: str = ""):
    """Upload a file attachment for use in chat messages."""
    data_dir = request.app.state.config_path.parent
    upload_dir = data_dir / "chat-files"
    upload_dir.mkdir(parents=True, exist_ok=True)

    file_id = uuid.uuid4().hex[:12]
    ext = Path(file.filename).suffix if file.filename else ""
    stored_name = f"{file_id}{ext}"
    dest = upload_dir / stored_name
    content = await file.read()
    dest.write_bytes(content)

    attachment = {
        "id": file_id,
        "filename": file.filename or "unnamed",
        "content_type": file.content_type or "application/octet-stream",
        "size": len(content),
        "url": f"/api/chat/files/{stored_name}",
    }
    return attachment


@router.get("/api/chat/files/{filename}")
async def serve_file(request: Request, filename: str):
    """Serve an uploaded chat file."""
    data_dir = request.app.state.config_path.parent
    file_path = data_dir / "chat-files" / filename
    if not file_path.exists() or not file_path.resolve().is_relative_to(
        (data_dir / "chat-files").resolve()
    ):
        return JSONResponse({"error": "File not found"}, status_code=404)
    return FileResponse(file_path)


# ── Search & unread ───────────────────────────────────────────────────────────

@router.get("/api/chat/search")
async def search_messages(
    request: Request, q: str = "", channel_id: str | None = None, limit: int = 20
):
    if not q or len(q) < 2:
        return {"results": [], "query": q}
    msg_store = request.app.state.chat_messages
    results = await msg_store.search(q, channel_id=channel_id, limit=limit)
    return {"results": results, "query": q}


@router.get("/api/chat/unread")
async def get_unread(request: Request):
    ch_store = request.app.state.chat_channels
    counts = await ch_store.get_unread_counts("user")
    return {"unread": counts}


@router.post("/api/chat/channels/{channel_id}/read-cursor/rewind")
async def rewind_read_cursor_endpoint(channel_id: str, request: Request):
    body = await request.json()
    before_id = body.get("before_message_id")
    if not before_id:
        return JSONResponse({"error": "before_message_id required"}, status_code=400)
    msg_store = request.app.state.chat_messages
    ch_store = request.app.state.chat_channels
    msg = await msg_store.get_message(before_id)
    if msg is None or msg["channel_id"] != channel_id:
        return JSONResponse({"error": "message not in channel"}, status_code=404)
    auth = getattr(request.app.state, "auth", None)
    session_user = None
    if auth is not None:
        token = request.cookies.get("taos_session") or ""
        session_user = auth.get_user(token=token)
    if session_user is None:
        return JSONResponse({"error": "not authenticated"}, status_code=401)
    await ch_store.rewind_read_cursor(
        session_user["id"], channel_id, msg["created_at"] - 0.001,
    )
    return JSONResponse({"ok": True})


@router.post("/api/chat/channels/{channel_id}/mark-read")
async def mark_read(request: Request, channel_id: str):
    body = await request.json()
    ch_store = request.app.state.chat_channels
    await ch_store.update_read_position("user", channel_id, body.get("message_id", ""))
    return {"status": "marked"}


# ── Reactions ────────────────────────────────────────────────────────────────

@router.post("/api/chat/messages/{message_id}/reactions")
async def add_reaction(message_id: str, body: dict, request: Request):
    emoji = body.get("emoji")
    author_id = body.get("author_id")
    author_type = body.get("author_type", "user")
    if not emoji or not author_id:
        return JSONResponse({"error": "emoji and author_id required"}, status_code=400)
    state = request.app.state
    msg = await state.chat_messages.get_message(message_id)
    if msg is None:
        return JSONResponse({"error": "message not found"}, status_code=404)
    await state.chat_messages.add_reaction(message_id, emoji, author_id)
    channel = await state.chat_channels.get_channel(msg["channel_id"])
    await state.chat_hub.broadcast(msg["channel_id"], {
        "type": "reaction_added",
        "message_id": message_id,
        "emoji": emoji,
        "author_id": author_id,
    })
    if channel is not None:
        await maybe_trigger_semantic(
            emoji=emoji, message=msg,
            reactor_id=author_id, reactor_type=author_type,
            channel=channel, state=state,
        )
    return JSONResponse({"ok": True}, status_code=200)


@router.delete("/api/chat/messages/{message_id}/reactions/{emoji}")
async def remove_reaction(message_id: str, emoji: str, author_id: str, request: Request):
    state = request.app.state
    await state.chat_messages.remove_reaction(message_id, emoji, author_id)
    msg = await state.chat_messages.get_message(message_id)
    if msg:
        await state.chat_hub.broadcast(msg["channel_id"], {
            "type": "reaction_removed",
            "message_id": message_id,
            "emoji": emoji,
            "author_id": author_id,
        })
    return JSONResponse({"ok": True}, status_code=200)


@router.get("/api/chat/channels/{channel_id}/wants_reply")
async def list_wants_reply(channel_id: str, request: Request):
    reg = getattr(request.app.state, "wants_reply", None)
    if reg is None:
        return JSONResponse({"slugs": []})
    return JSONResponse({"slugs": reg.list(channel_id)})


VALID_PHASES = {"thinking", "tool", "reading", "writing", "searching", "planning"}

# ── Typing / thinking indicators ─────────────────────────────────────────────

@router.post("/api/chat/channels/{channel_id}/typing")
async def post_typing(channel_id: str, body: dict, request: Request):
    """Mark a human user as typing in the channel. Ephemeral; TTL 3s."""
    author_id = (body or {}).get("author_id")
    if not author_id:
        return JSONResponse({"error": "author_id required"}, status_code=400)
    reg = getattr(request.app.state, "typing", None)
    hub = getattr(request.app.state, "chat_hub", None)
    if reg is None:
        return JSONResponse({"error": "typing registry not configured"}, status_code=503)
    reg.mark(channel_id, author_id, "human")
    if hub is not None:
        await hub.broadcast(channel_id, {
            "type": "typing",
            "kind": "human",
            "slug": author_id,
        })
    return JSONResponse({"ok": True}, status_code=200)


@router.post("/api/chat/channels/{channel_id}/thinking")
async def post_thinking(channel_id: str, body: dict, request: Request):
    """Bridge-side heartbeat: state=start marks the agent as thinking,
    state=end clears. Authenticated with the local bearer token."""
    auth = getattr(request.app.state, "auth", None)
    bearer = request.headers.get("authorization", "")
    if not bearer.lower().startswith("bearer "):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    if auth is None or not auth.validate_local_token(bearer[7:].strip()):
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    slug = (body or {}).get("slug")
    state = (body or {}).get("state")
    if not slug or state not in ("start", "end"):
        return JSONResponse({"error": "slug and state in {start,end} required"}, status_code=400)

    phase = (body or {}).get("phase")
    if phase is not None:
        if not isinstance(phase, str) or phase not in VALID_PHASES:
            return JSONResponse(
                {"error": f"invalid phase; must be one of {sorted(VALID_PHASES)}"},
                status_code=400,
            )
    detail = (body or {}).get("detail")
    if detail is not None and not isinstance(detail, str):
        return JSONResponse({"error": "detail must be a string"}, status_code=400)

    reg = getattr(request.app.state, "typing", None)
    hub = getattr(request.app.state, "chat_hub", None)
    if reg is None:
        return JSONResponse({"error": "typing registry not configured"}, status_code=503)
    if state == "start":
        reg.mark(channel_id, slug, "agent", phase=phase, detail=detail)
    else:
        reg.clear(channel_id, slug)
    if hub is not None:
        await hub.broadcast(channel_id, {
            "type": "thinking",
            "slug": slug,
            "state": state,
            "phase": phase if state == "start" else None,
            "detail": detail if state == "start" else None,
        })
    return JSONResponse({"ok": True}, status_code=200)


@router.get("/api/chat/channels/{channel_id}/typing")
async def get_typing(channel_id: str, request: Request):
    """Return current typing+thinking state for a channel."""
    reg = getattr(request.app.state, "typing", None)
    if reg is None:
        return JSONResponse({"human": [], "agent": []})
    return JSONResponse(reg.list(channel_id))


# ── Canvas ───────────────────────────────────────────────────────────────────

@router.post("/api/canvas/generate")
async def create_canvas(request: Request):
    """Create a new canvas page."""
    body = await request.json()
    canvas_store = request.app.state.canvas_store
    canvas = await canvas_store.create(
        title=body.get("title", "Untitled"),
        content=body.get("content", ""),
        style=body.get("style", "auto"),
        format=body.get("format", "markdown"),
        created_by=body.get("agent_name", "system"),
    )
    return {
        "canvas_id": canvas["id"],
        "canvas_url": f"/canvas/{canvas['id']}",
        "edit_token": canvas["edit_token"],
    }


@router.post("/api/canvas/{canvas_id}/update")
async def update_canvas(request: Request, canvas_id: str):
    """Update canvas content (requires edit_token)."""
    body = await request.json()
    canvas_store = request.app.state.canvas_store
    updated = await canvas_store.update(
        canvas_id,
        edit_token=body.get("edit_token", ""),
        content=body.get("content"),
        title=body.get("title"),
    )
    if not updated:
        return JSONResponse({"error": "Invalid edit token or canvas not found"}, status_code=403)
    # Broadcast to canvas viewers
    hub = request.app.state.chat_hub
    canvas = await canvas_store.get(canvas_id)
    await hub.broadcast(f"canvas:{canvas_id}", {"type": "canvas_update", "content": canvas["content"], "title": canvas["title"]})
    return {"status": "updated"}


@router.get("/api/canvas/{canvas_id}/data")
async def canvas_data(request: Request, canvas_id: str):
    """Get canvas data as JSON."""
    canvas_store = request.app.state.canvas_store
    canvas = await canvas_store.get(canvas_id)
    if not canvas:
        return JSONResponse({"error": "Canvas not found"}, status_code=404)
    return canvas


@router.delete("/api/canvas/{canvas_id}")
async def delete_canvas(request: Request, canvas_id: str):
    canvas_store = request.app.state.canvas_store
    deleted = await canvas_store.delete(canvas_id)
    if not deleted:
        return JSONResponse({"error": "Canvas not found"}, status_code=404)
    return {"status": "deleted"}


@router.get("/api/canvas")
async def list_canvases(request: Request, limit: int = 50):
    canvas_store = request.app.state.canvas_store
    canvases = await canvas_store.list_all(limit=limit)
    return {"canvases": canvases}


@router.websocket("/ws/canvas/{canvas_id}")
async def canvas_ws(websocket: WebSocket, canvas_id: str):
    """WebSocket for live canvas updates."""
    await websocket.accept()
    hub = websocket.app.state.chat_hub
    canvas_channel = f"canvas:{canvas_id}"
    hub.join(websocket, canvas_channel)
    try:
        while True:
            await websocket.receive_text()  # Keep connection alive
    except WebSocketDisconnect:
        pass
    finally:
        hub.leave(websocket, canvas_channel)


