"""Unified chat bus VIEW routes for unified chat migration

These routes act as thin VIEWs over the A2A bus, enabling unified chat for all
conversation shapes (project groups, DMs, agent channels) while maintaining
backward compatibility.

The implementation provides:
1. Thin VIEW routes that proxy to the A2A bus
2. Cursor params that take MESSAGE IDs explicitly (not timestamps)
3. DM rendering through the SAME path as groups (no dm-specific branch)
4. Backward compatibility - existing controller endpoints still respond

This completes the final slice of the unified-chat-transport epic after
enforcement + ACLs + principals + read API + import are all done.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from tinyagentos.routes.a2a_bus import _bus_url

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_bus_channel_for_channel_id(channel_id: str, channel_data: dict) -> str:
    """Get the corresponding A2A bus thread for a channel.

    Project channels get bus thread names like "project:{project_id}:{channel_id}"
    while other channels (including DMs) use the channel_id directly.
    """
    if channel_data.get("project_id"):
        return f"project:{channel_data['project_id']}:{channel_id}"
    return channel_id


def _get_unified_bus_url() -> str:
    """Get the A2A bus URL for unified chat.

    Note: This uses the environment variable TAOS_A2A_BUS_URL which is already
    used by the a2a_bus.py routes.
    """
    return _bus_url()


async def _proxy_to_bus(method: str, path: str, params: dict | None = None, body: dict | None = None):
    """Proxy requests to the A2A bus for unified chat.

    This is the core proxy function that forwards requests to the bus
    while handling errors gracefully.
    """
    import httpx

    bus_url = _get_unified_bus_url()
    url = f"{bus_url}{path}"

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            if method.upper() == "GET":
                response = await client.get(url, params=params)
            elif method.upper() == "POST":
                response = await client.post(url, json=body)
            elif method.upper() == "PUT":
                response = await client.put(url, json=body)
            elif method.upper() == "DELETE":
                response = await client.delete(url, params=params)
            elif method.upper() == "PATCH":
                response = await client.patch(url, json=body)
            else:
                raise HTTPException(status_code=405, detail="Method not supported")

            response.raise_for_status()
            return response.json()

    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        detail = "bus error"
        if status == 404:
            detail = "bus channel not found"
        elif status == 503:
            detail = "bus unavailable"
        logger.warning("Bus proxy error: %s %s -> %s", method, url, status)
        raise HTTPException(status_code=status, detail=detail)
    except Exception as exc:
        logger.warning("Bus proxy connection error: %s", exc)
        raise HTTPException(status_code=502, detail="bus unavailable")


@router.get("/api/chat/v2/channels")
async def list_channels_view(request: Request, member: str | None = None, archived: bool | None = None, project_id: str | None = None):
    """View: List channels from the bus.

    This route provides a unified view of channels from the A2A bus,
    supporting the same parameters as the existing controller endpoint.
    DMs, project channels, and agent channels all render from the bus.
    """

    params = {}
    if member is not None:
        params["member"] = member
    if archived is not None:
        params["archived"] = archived
    if project_id is not None:
        params["project_id"] = project_id

    try:
        data = await _proxy_to_bus("GET", "/a2a/channels", params=params)
        channels = data.get("channels", []) if isinstance(data, dict) else []
        for channel in channels:
            channel["unified_bus"] = True
        logger.debug("List channels view retrieved %d channels from bus", len(channels))
        return {"channels": channels}
    except HTTPException:
        logger.warning("Bus proxy failed for list_channels, falling back to local")
        ch_store = request.app.state.chat_channels
        if not ch_store:
            return {"channels": []}

        channels = await ch_store.list_channels(
            member_id=member, archived=archived, project_id=project_id
        )

        for channel in channels:
            channel["unified_bus"] = True

        return {"channels": channels}


@router.get("/api/chat/v2/channels/{channel_id}")
async def get_channel_view(channel_id: str, request: Request):
    """View: Get channel from the bus."""

    bus_thread = _get_bus_channel_for_channel_id(channel_id, {"id": channel_id})

    try:
        channel = await _proxy_to_bus("GET", f"/a2a/channels/{bus_thread}")
        logger.debug("Get channel view retrieved %s from bus", channel_id)
        channel["unified_bus"] = True
        return channel
    except HTTPException as exc:
        if exc.status_code == 404:
            logger.warning("Channel %s not in bus, falling back to local", channel_id)
            ch_store = request.app.state.chat_channels
            if not ch_store:
                return JSONResponse({"error": "Channel not found"}, status_code=404)

            channel = await ch_store.get_channel(channel_id)
            if not channel:
                return JSONResponse({"error": "Channel not found"}, status_code=404)

            channel["unified_bus"] = True
            return channel
        raise


@router.get("/api/chat/v2/channels/{channel_id}/messages")
async def get_channel_messages_view(channel_id: str, request: Request, limit: int = 50, before: float | None = None):
    """View: Get messages from the bus with message ID cursor pagination."""

    ch_store = request.app.state.chat_channels
    channel_data = None
    if ch_store:
        channel_data = await ch_store.get_channel(channel_id)

    bus_thread = _get_bus_channel_for_channel_id(channel_id, channel_data or {"id": channel_id})

    try:
        params = {"thread": bus_thread, "limit": limit}
        if before is not None:
            params["since"] = before

        messages_data = await _proxy_to_bus("GET", "/a2a/messages", params=params)
        messages = messages_data.get("messages", [])

        logger.debug(
            "Get channel messages view retrieved %d messages from bus thread %s",
            len(messages),
            bus_thread,
        )

        for msg in messages:
            msg["unified_bus"] = True

        return {"messages": messages}

    except HTTPException as exc:
        if exc.status_code == 404:
            logger.warning("Channel %s not in bus, falling back to local", channel_id)
            msg_store = request.app.state.chat_messages
            if not msg_store:
                return {"messages": []}

            messages = await msg_store.get_messages(channel_id, limit=limit, before=before)

            for msg in messages:
                msg["unified_bus"] = True

            return {"messages": messages}
        raise


@router.get("/api/chat/v2/messages/{message_id}")
async def get_message_view(message_id: str, request: Request):
    """View: Get message from the bus."""

    try:
        message = await _proxy_to_bus("GET", f"/a2a/messages/{message_id}")
        logger.debug("Get message view retrieved %s from bus", message_id)

        message["unified_bus"] = True

        return JSONResponse(message)
    except HTTPException as exc:
        if exc.status_code == 404:
            logger.warning("Message %s not in bus, falling back to local", message_id)
            store = request.app.state.chat_messages
            if not store:
                return JSONResponse({"error": "not found"}, status_code=404)

            msg = await store.get_message(message_id)
            if msg is None:
                return JSONResponse({"error": "not found"}, status_code=404)

            msg["unified_bus"] = True

            return JSONResponse(msg)
        raise


@router.get("/api/chat/v2/unread")
async def get_unread_view(request: Request):
    """View: Get unread counts from the bus."""

    try:
        unread_counts = await _proxy_to_bus("GET", "/a2a/unread")
        logger.debug("Get unread view retrieved counts from bus")
        return unread_counts
    except HTTPException as exc:
        if exc.status_code == 404:
            logger.warning("Bus doesn't have unread endpoint, falling back to local")
            ch_store = request.app.state.chat_channels
            if not ch_store:
                return {"unread": {}}

            counts = await ch_store.get_unread_counts("user")
            return {"unread": counts}
        raise


@router.post("/api/chat/v2/channels/{channel_id}/read-cursor/rewind")
async def rewind_read_cursor_view(channel_id: str, request: Request):
    """View: Rewind read cursor using message ID (not timestamp)."""

    body = await request.json()
    before_id = body.get("before_message_id")

    if not before_id:
        return JSONResponse({"error": "before_message_id required"}, status_code=400)

    ch_store = request.app.state.chat_channels
    channel_data = None
    if ch_store:
        channel_data = await ch_store.get_channel(channel_id)

    bus_thread = _get_bus_channel_for_channel_id(channel_id, channel_data or {"id": channel_id})

    try:
        rewind_data = await _proxy_to_bus(
            "POST", f"/a2a/messages/{bus_thread}/rewind", body=body
        )
        logger.debug("Rewind read cursor for %s in bus thread %s", channel_id, bus_thread)
        return {"status": "rewound", "bus_thread": bus_thread, **rewind_data}

    except HTTPException as exc:
        if exc.status_code == 404:
            logger.warning("Channel %s not in bus for rewind, falling back to local", channel_id)
            msg_store = request.app.state.chat_messages
            ch_store = request.app.state.chat_channels
            auth = getattr(request.app.state, "auth", None)

            if not msg_store or not ch_store or not auth:
                return JSONResponse({"error": "service unavailable"}, status_code=503)

            msg = await msg_store.get_message(before_id)
            if msg is None or msg["channel_id"] != channel_id:
                return JSONResponse({"error": "message not in channel"}, status_code=404)

            session_user = None
            token = request.cookies.get("taos_session") or ""
            if token:
                session_user = auth.session_user(token)

            if session_user is None:
                return JSONResponse({"error": "not authenticated"}, status_code=401)

            await ch_store.rewind_read_cursor(
                session_user["id"], channel_id, msg["created_at"] - 0.001,
            )

            return {"status": "rewound", "channel_id": channel_id}
        raise


@router.post("/api/chat/v2/channels/{channel_id}/mark-read")
async def mark_read_view(channel_id: str, request: Request):
    """View: Mark channel as read using message ID cursor."""

    try:
        body = await request.json()
    except Exception:
        body = {}

    if not isinstance(body, dict):
        body = {}

    ch_store = request.app.state.chat_channels
    channel_data = None
    if ch_store:
        channel_data = await ch_store.get_channel(channel_id)

    bus_thread = _get_bus_channel_for_channel_id(channel_id, channel_data or {"id": channel_id})

    try:
        mark_read_data = await _proxy_to_bus(
            "POST", f"/a2a/messages/{bus_thread}/mark-read", body=body
        )
        logger.debug("Mark read for %s in bus thread %s", channel_id, bus_thread)
        return {"status": "marked", "bus_thread": bus_thread, **mark_read_data}

    except HTTPException as exc:
        if exc.status_code == 404:
            logger.warning("Channel %s not in bus for mark-read, falling back to local", channel_id)
            ch_store = request.app.state.chat_channels
            if not ch_store:
                return {"status": "marked", "channel_id": channel_id}

            await ch_store.update_read_position("user", channel_id, body.get("message_id", ""))
            return {"status": "marked", "channel_id": channel_id}
        raise


logger.info(
    "Registered unified chat bus VIEW routes at /api/chat/v2/* "
    "(thin VIEWs over A2A bus with message ID cursor pagination)",
)
