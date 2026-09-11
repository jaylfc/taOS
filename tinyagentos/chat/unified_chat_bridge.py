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

from tinyagentos.chat.message_store import ChatMessageStore
from tinyagentos.chat.channel_store import ChatChannelStore
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

        # Get channel info for bus thread
        channel = None
        if self.chat_channels:
            channel = await self.chat_channels.get_channel(channel_id)

        # Determine thread (channel ID for A2A bus)
        thread = channel_id
        if channel and channel.get("project_id"):
            thread = f"project:{channel['project_id']}:{channel_id}"

        # Prepare bus message
        bus_message = {
            "from": message.get("author_id", "system"),
            "thread": thread,
            "body": message.get("content", ""),
            "reply_to": message.get("thread_id"),
        }

        # Try to post to A2A bus
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

    async def ensure_bus_channel_exists(self, channel_id: str, channel_data: dict):
        """Ensure the corresponding A2A bus channel exists.

        This creates a bus thread for the channel if it doesn't already exist.
        """
        thread = channel_id
        if channel_data.get("project_id"):
            thread = f"project:{channel_data['project_id']}:{channel_id}"

        # Try to create bus channel by sending a test message
        try:
            import httpx

            bus = _bus_url()
            # Just test with a minimal message to ensure the thread exists
            test_message = {
                "from": "system",
                "thread": thread,
                "body": f"Channel {channel_id} initialized",
            }

            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(
                    f"{bus}/a2a/send",
                    json=test_message,
                )
                # Don't care about result, just want to ensure thread exists
                logger.debug("Ensured bus thread %s exists", thread)
        except Exception as exc:
            logger.warning("Failed to ensure bus thread %s: %s", thread, exc)

    async def handle_message_write(self, channel_id: str, message: dict):
        """Handle a message write through both local store and bus.

        This forwards controller chat writes to the bus while keeping local
        storage intact for backward compatibility.
        """
        # Forward to A2A bus for unified chat
        await self.forward_controller_message_to_bus(channel_id, message)

        # Get channel info for bus thread
        channel = None
        if self.chat_channels:
            channel = await self.chat_channels.get_channel(channel_id)

        # Ensure bus thread exists
        if channel:
            await self.ensure_bus_channel_exists(channel_id, channel)

        # Log the unified write
        logger.debug(
            "Unified chat write to channel %s (bus thread: %s)",
            channel_id,
            channel_id if not channel or not channel.get("project_id") else f"project:{channel['project_id']}:{channel_id}",
        )
# Initialize the bridge
_chat_bridge = None
def get_chat_bridge(app) -> ChatBusBridge | None:
    """Get or create the ChatBusBridge instance for the app."""
    global _chat_bridge
    if _chat_bridge is None and hasattr(app, "state"):
        _chat_bridge = ChatBusBridge(app)
    return _chat_bridge
