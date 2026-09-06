from __future__ import annotations
import asyncio
import logging
import httpx
from fastapi import Request
from tinyagentos.channel_hub.message import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class WebhookConnector:
    def __init__(self, agent_name: str, router, outgoing_url: str | None = None):
        self.agent_name = agent_name
        self.router = router
        self.outgoing_url = outgoing_url  # URL to forward responses to

    async def handle_incoming(self, request_data: dict) -> dict:
        """Handle an incoming webhook POST. Returns the agent's response."""
        incoming = IncomingMessage(
            id=request_data.get("id", ""),
            from_id=request_data.get("from", "webhook"),
            from_name=request_data.get("name", "Webhook"),
            platform="webhook",
            channel_id=request_data.get("channel", "default"),
            channel_name="Webhook",
            text=request_data.get("text", request_data.get("message", "")),
            raw=request_data,
        )
        response = await self.router.route_message(self.agent_name, incoming)
        if not response:
            return {"status": "no_response"}

        result = {
            "content": response.content,
            "buttons": response.buttons,
            "images": response.images,
        }

        # Forward to outgoing URL if configured
        if self.outgoing_url and response.content:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(self.outgoing_url, json=result)
            except Exception as e:
                logger.warning(f"Outgoing webhook failed: {e}")

        return result
