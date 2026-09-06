from __future__ import annotations
import asyncio
import logging
import httpx
from tinyagentos.channel_hub.message import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class DiscordConnector:
    def __init__(self, bot_token: str, agent_name: str, router, channel_ids: list[str] | None = None):
        self.bot_token = bot_token
        self.agent_name = agent_name
        self.router = router
        self.channel_ids = channel_ids or []  # channels to monitor
        self.base_url = "https://discord.com/api/v10"
        self.headers = {"Authorization": f"Bot {bot_token}"}
        self._running = False
        self._last_message_ids: dict[str, str] = {}
        self._task = None
        self._bot_user_id: str | None = None

    async def start(self):
        self._running = True
        # Get bot user ID
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{self.base_url}/users/@me", headers=self.headers)
            if resp.status_code == 200:
                self._bot_user_id = resp.json().get("id")
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"Discord connector started for agent '{self.agent_name}'")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _poll_loop(self):
        async with httpx.AsyncClient(timeout=15) as client:
            while self._running:
                try:
                    for channel_id in self.channel_ids:
                        await self._check_channel(client, channel_id)
                    await asyncio.sleep(2)  # Poll every 2s
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Discord poll error: {e}")
                    await asyncio.sleep(5)

    async def _check_channel(self, client: httpx.AsyncClient, channel_id: str):
        params: dict = {"limit": 10}
        last_id = self._last_message_ids.get(channel_id)
        if last_id:
            params["after"] = last_id

        resp = await client.get(
            f"{self.base_url}/channels/{channel_id}/messages",
            headers=self.headers, params=params,
        )
        if resp.status_code != 200:
            return

        messages = resp.json()
        if not messages:
            return

        # Update last seen ID
        self._last_message_ids[channel_id] = messages[0]["id"]

        # Process in chronological order (API returns newest first)
        for msg in reversed(messages):
            if msg.get("author", {}).get("id") == self._bot_user_id:
                continue  # Skip own messages
            await self._handle_message(client, channel_id, msg)

    async def _handle_message(self, client: httpx.AsyncClient, channel_id: str, msg: dict):
        incoming = IncomingMessage(
            id=msg["id"],
            from_id=msg.get("author", {}).get("id", ""),
            from_name=msg.get("author", {}).get("username", "User"),
            platform="discord",
            channel_id=channel_id,
            channel_name=f"discord-{channel_id}",
            text=msg.get("content", ""),
            raw=msg,
        )

        response = await self.router.route_message(self.agent_name, incoming)
        if response:
            await self._send_response(client, channel_id, response)

    async def _send_response(self, client: httpx.AsyncClient, channel_id: str, response: OutgoingMessage):
        if response.passthrough and response.passthrough_platform == "discord":
            payload = response.passthrough_payload
            await client.post(f"{self.base_url}/channels/{channel_id}/messages",
                              headers=self.headers, json=payload)
            return

        # Build payload for rich content
        payload: dict = {}

        if response.content:
            payload["content"] = response.content

        # Add embeds for images
        if response.images:
            payload["embeds"] = [{"image": {"url": img}} for img in response.images if img.startswith("http")]

        # Add components for buttons
        if response.buttons:
            payload["components"] = [{
                "type": 1,  # ACTION_ROW
                "components": [
                    {"type": 2, "style": 1, "label": b["label"], "custom_id": b.get("action", b["label"])}
                    for b in response.buttons[:5]  # Discord max 5 buttons per row
                ]
            }]

        if payload:
            await client.post(f"{self.base_url}/channels/{channel_id}/messages",
                              headers=self.headers, json=payload)
