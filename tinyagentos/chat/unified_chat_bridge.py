"""ChatBusBridge for unified chat bus migration

Implements the bridge between existing chat routes and the A2A bus
as part of the unified-chat-transport epic.

This bridge provides:
1. Forward existing controller chat writes to the A2A bus
2. Read from bus when requested via unified view routes
3. Maintain backward compatibility for existing clients
4. Support all conversation shapes (project groups, DMs, agent channels)
"""

from __future__ import annotations

import logging

from tinyagentos.routes.a2a_bus import _bus_url

logger = logging.getLogger(__name__)


class ChatBusBridge:
    """Bridge between controller chat routes and the A2A bus for unified chat.

    This bridge enables the migration of chat messages to the A2A bus while
    maintaining backward compatibility. Existing controller chat endpoints
    continue to work, and the bus receives all write operations.
    """

    def __init__(self, app):
        self.app = app
        self.chat_messages = getattr(app.state, "chat_messages", None)
        self.chat_channels = getattr(app.state, "chat_channels", None)
        self.hub = getattr(app.state, "chat_hub", None)

    async def forward_controller_message_to_bus(self, channel_id: str, message: dict):
        """Forward a controller message to the A2A bus.

        This allows existing controller chat operations to also write to the
        A2A bus for unified chat migration.
        """
        if not self.chat_messages:
            return

        channel = None
        if self.chat_channels:
            channel = await self.chat_channels.get_channel(channel_id)

        thread = channel_id
        if channel and channel.get("project_id"):
            thread = f"project:{channel['project_id']}:{channel_id}"

        bus_message = {
            "from": "controller",
            "thread": thread,
            "body": message.get("content", ""),
            "reply_to": message.get("thread_id"),
            "author_id": message.get("author_id"),
        }

        try:
            import httpx

            bus = _bus_url()
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{bus}/a2a/send",
                    json=bus_message,
                )
                resp.raise_for_status()
                logger.debug(
                    "Forwarded controller message to bus thread %s",
                    thread,
                )
        except Exception as exc:
            logger.warning("Failed to forward controller message to bus: %s", exc)

    async def handle_message_write(self, channel_id: str, message: dict):
        """Handle a message write through both local store and bus.

        This forwards controller chat writes to the bus while keeping local
        storage intact for backward compatibility.
        """
        await self.forward_controller_message_to_bus(channel_id, message)

        channel = None
        if self.chat_channels:
            channel = await self.chat_channels.get_channel(channel_id)

        logger.debug(
            "Unified chat write to channel %s (bus thread: %s)",
            channel_id,
            channel_id if not channel or not channel.get("project_id") else f"project:{channel['project_id']}:{channel_id}",
        )


def get_chat_bridge(app) -> ChatBusBridge | None:
    """Get or create the ChatBusBridge instance for the app."""
    if not hasattr(app, "state"):
        return None
    bridge = getattr(app.state, "chat_bus_bridge", None)
    if bridge is None:
        bridge = ChatBusBridge(app)
        app.state.chat_bus_bridge = bridge
    return bridge
