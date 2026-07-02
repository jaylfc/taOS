from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["channel-hub"])

# One lock per connector key, held across stop-old + start-new + map-assign
# (and around disconnects). Two concurrent connects for the same key would
# otherwise both pop (second gets None), both start, and the loser's
# connector stays alive but unreachable via the map, so it could never be
# stopped. Refcounted so an entry is evicted once no task holds or awaits it
# (the map must not grow forever); the bookkeeping has no awaits, so it is
# atomic under asyncio's single-threaded scheduling.
_connect_locks: dict[str, asyncio.Lock] = {}
_connect_lock_refs: dict[str, int] = {}


@asynccontextmanager
async def _connect_lock(connector_key: str):
    _connect_lock_refs[connector_key] = _connect_lock_refs.get(connector_key, 0) + 1
    lock = _connect_locks.setdefault(connector_key, asyncio.Lock())
    try:
        async with lock:
            yield
    finally:
        remaining = _connect_lock_refs[connector_key] - 1
        if remaining:
            _connect_lock_refs[connector_key] = remaining
        else:
            del _connect_lock_refs[connector_key]
            _connect_locks.pop(connector_key, None)


async def _stop_prior_connector(connectors: dict, connector_key: str) -> None:
    """Stop and drop any prior connector under this key. Reconnecting (e.g.
    after a token rotation) must not silently orphan the previous connector's
    background task/client.

    The pop happens before the await on purpose: callers hold the per-key
    connect lock, so nothing can observe the gap, and the caller is about to
    overwrite the slot with a fresh connector either way. If stop() raises,
    the old connector's task may live until process restart; that is logged
    loudly rather than blocking the reconnect, because keeping a broken
    connector registered would be strictly worse."""
    old = connectors.pop(connector_key, None)
    if old is not None and hasattr(old, "stop"):
        try:
            await old.stop()
        except Exception:
            logger.error(
                "channel-hub: failed to stop prior %s connector on reconnect; "
                "its background task may persist until restart",
                connector_key,
                exc_info=True,
            )


@router.get("/api/channel-hub/status")
async def channel_hub_status(request: Request):
    """Show active connectors, adapters, and message counts."""
    router_obj = request.app.state.channel_hub_router
    adapter_mgr = request.app.state.adapter_manager
    connectors = getattr(request.app.state, "channel_hub_connectors", {})

    return {
        "connectors": {
            name: {"platform": c.__class__.__name__.replace("Connector", "").lower(), "agent": c.agent_name}
            for name, c in connectors.items()
        },
        "adapters": {
            name: {"port": port}
            for name, port in router_obj._agent_ports.items()
        },
        "channel_assignments": dict(router_obj._channel_assignments),
    }


@router.post("/api/channel-hub/connect")
async def connect_bot(request: Request):
    """Connect a Telegram/Discord/WebChat bot. Body: {platform, bot_token_secret, agent_name, ...}."""
    body = await request.json()
    platform = body.get("platform", "")
    agent_name = body.get("agent_name", "")

    # WebChat does not need a bot token secret
    if platform == "webchat":
        if not agent_name:
            return JSONResponse({"error": "agent_name is required"}, status_code=400)
        router_obj = request.app.state.channel_hub_router
        connectors = getattr(request.app.state, "channel_hub_connectors", {})
        connector_key = f"webchat:{agent_name}"

        from tinyagentos.channel_hub.webchat_connector import WebChatConnector
        async with _connect_lock(connector_key):
            await _stop_prior_connector(connectors, connector_key)
            connector = WebChatConnector(agent_name=agent_name, router=router_obj)
            connectors[connector_key] = connector
            request.app.state.channel_hub_connectors = connectors
        return {"status": "connected", "platform": "webchat", "agent_name": agent_name}

    bot_token_secret = body.get("bot_token_secret", "")

    if not platform or not bot_token_secret or not agent_name:
        return JSONResponse({"error": "platform, bot_token_secret, and agent_name are required"}, status_code=400)

    # Resolve the bot token from secrets store
    secrets_store = request.app.state.secrets
    secret_record = await secrets_store.get(bot_token_secret)
    if not secret_record:
        return JSONResponse({"error": f"Secret '{bot_token_secret}' not found"}, status_code=404)
    bot_token = secret_record.get("value", "")

    router_obj = request.app.state.channel_hub_router
    connectors = getattr(request.app.state, "channel_hub_connectors", {})

    connector_key = f"{platform}:{agent_name}"

    # Validate required per-platform inputs BEFORE stopping the live
    # connector: a malformed reconnect must fail without tearing down the
    # working one.
    homeserver = body.get("homeserver", "") if platform == "matrix" else ""
    if platform == "matrix" and not homeserver:
        return JSONResponse({"error": "homeserver is required for matrix"}, status_code=400)

    async with _connect_lock(connector_key):
        await _stop_prior_connector(connectors, connector_key)

        if platform == "telegram":
            from tinyagentos.channel_hub.telegram_connector import TelegramConnector
            connector = TelegramConnector(bot_token=bot_token, agent_name=agent_name, router=router_obj)
            router_obj.assign_channel(platform, bot_token_secret, agent_name)
            await connector.start()
            connectors[connector_key] = connector
            request.app.state.channel_hub_connectors = connectors
            return {"status": "connected", "platform": platform, "agent_name": agent_name}
        elif platform == "discord":
            channel_ids = body.get("channel_ids", [])
            from tinyagentos.channel_hub.discord_connector import DiscordConnector
            connector = DiscordConnector(
                bot_token=bot_token, agent_name=agent_name,
                router=router_obj, channel_ids=channel_ids,
            )
            router_obj.assign_channel(platform, bot_token_secret, agent_name)
            await connector.start()
            connectors[connector_key] = connector
            request.app.state.channel_hub_connectors = connectors
            return {"status": "connected", "platform": platform, "agent_name": agent_name}
        elif platform == "slack":
            channel_ids = body.get("channel_ids", [])
            from tinyagentos.channel_hub.slack_connector import SlackConnector
            connector = SlackConnector(
                bot_token=bot_token, agent_name=agent_name,
                router=router_obj, channel_ids=channel_ids,
            )
            router_obj.assign_channel(platform, bot_token_secret, agent_name)
            await connector.start()
            connectors[connector_key] = connector
            request.app.state.channel_hub_connectors = connectors
            return {"status": "connected", "platform": platform, "agent_name": agent_name}
        elif platform == "email":
            imap_host = body.get("imap_host", "")
            imap_port = body.get("imap_port", 993)
            smtp_host = body.get("smtp_host", "")
            smtp_port = body.get("smtp_port", 587)
            # bot_token holds "username:password" for email
            parts = bot_token.split(":", 1)
            email_user = parts[0]
            email_pass = parts[1] if len(parts) > 1 else ""
            from tinyagentos.channel_hub.email_connector import EmailConnector
            connector = EmailConnector(
                agent_name=agent_name, router=router_obj,
                imap_host=imap_host, imap_port=imap_port,
                smtp_host=smtp_host, smtp_port=smtp_port,
                username=email_user, password=email_pass,
            )
            router_obj.assign_channel(platform, bot_token_secret, agent_name)
            await connector.start()
            connectors[connector_key] = connector
            request.app.state.channel_hub_connectors = connectors
            return {"status": "connected", "platform": platform, "agent_name": agent_name}
        elif platform == "github":
            if not agent_name:
                return JSONResponse({"error": "agent_name is required"}, status_code=400)
            repo = body.get("repo")
            event_kinds = body.get("event_kinds", [])
            pr_number = body.get("pr_number")
            from tinyagentos.channel_hub.adapters.github import GithubConnector
            connector = GithubConnector(
                agent_name=agent_name, router=router_obj,
                repo=repo, event_kinds=event_kinds, pr_number=pr_number,
            )
            router_obj.assign_channel(platform, agent_name, agent_name)
            await connector.start()
            connectors[connector_key] = connector
            request.app.state.channel_hub_connectors = connectors
            return {"status": "connected", "platform": platform, "agent_name": agent_name}
        elif platform == "matrix":
            # homeserver validated above, before the destructive stop.
            from tinyagentos.channel_hub.matrix_connector import MatrixConnector
            connector = MatrixConnector(
                homeserver=homeserver,
                access_token=bot_token,
                agent_name=agent_name,
                router=router_obj,
            )
            router_obj.assign_channel(platform, bot_token_secret, agent_name)
            await connector.start()
            connectors[connector_key] = connector
            request.app.state.channel_hub_connectors = connectors
            return {"status": "connected", "platform": platform, "agent_name": agent_name}
        else:
            return JSONResponse({"error": f"Platform '{platform}' not yet supported"}, status_code=400)


@router.post("/api/channel-hub/disconnect")
async def disconnect_bot(request: Request):
    """Disconnect a bot. Body: {platform, agent_name}."""
    body = await request.json()
    platform = body.get("platform", "")
    agent_name = body.get("agent_name", "")

    connectors = getattr(request.app.state, "channel_hub_connectors", {})
    connector_key = f"{platform}:{agent_name}"

    # Same per-key lock as connect_bot: a disconnect racing a reconnect must
    # not pop the old connector mid-swap (404ing while the new one lands).
    async with _connect_lock(connector_key):
        connector = connectors.pop(connector_key, None)
        if connector:
            if hasattr(connector, "stop"):
                await connector.stop()
            request.app.state.channel_hub_connectors = connectors
            return {"status": "disconnected", "platform": platform, "agent_name": agent_name}
        else:
            return JSONResponse({"error": "Connector not found"}, status_code=404)


@router.get("/api/channel-hub/adapters")
async def list_adapters(request: Request):
    """List running adapters."""
    router_obj = request.app.state.channel_hub_router
    adapter_mgr = request.app.state.adapter_manager

    adapters = []
    for name, port in router_obj._agent_ports.items():
        running = name in adapter_mgr._processes and adapter_mgr._processes[name].poll() is None
        adapters.append({
            "agent_name": name,
            "port": port,
            "running": running,
        })

    return {"adapters": adapters}


@router.websocket("/ws/chat/{agent_name}")
async def webchat_ws(websocket: WebSocket, agent_name: str):
    """WebSocket endpoint for web chat."""
    auth_mgr = websocket.app.state.auth
    token = websocket.cookies.get("taos_session", "")
    user_id = auth_mgr.validate_session(token) if token else None
    if user_id is None:
        await websocket.close(code=1008)
        return

    connectors = getattr(websocket.app.state, "channel_hub_connectors", {})
    connector_key = f"webchat:{agent_name}"

    connector = connectors.get(connector_key)
    if not connector:
        # Auto-create a webchat connector if none exists
        from tinyagentos.channel_hub.webchat_connector import WebChatConnector
        router_obj = websocket.app.state.channel_hub_router
        connector = WebChatConnector(agent_name=agent_name, router=router_obj)
        connectors[connector_key] = connector
        websocket.app.state.channel_hub_connectors = connectors

    await connector.handle_websocket(websocket, user_id=user_id)


@router.post("/api/channel-hub/webhook/{agent_name}")
async def webhook_incoming(request: Request, agent_name: str):
    """Accept an incoming webhook and return the agent's response."""
    connectors = getattr(request.app.state, "channel_hub_connectors", {})
    connector_key = f"webhook:{agent_name}"

    connector = connectors.get(connector_key)
    if not connector:
        # Auto-create a webhook connector if none exists
        from tinyagentos.channel_hub.webhook_connector import WebhookConnector
        router_obj = request.app.state.channel_hub_router
        connector = WebhookConnector(agent_name=agent_name, router=router_obj)
        connectors[connector_key] = connector
        request.app.state.channel_hub_connectors = connectors

    body = await request.json()
    result = await connector.handle_incoming(body)
    return result
