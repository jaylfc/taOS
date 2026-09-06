from __future__ import annotations
import asyncio
import logging
import httpx
from tinyagentos.channel_hub.message import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class SlackConnector:
    def __init__(self, bot_token: str, agent_name: str, router, channel_ids: list[str] | None = None):
        self.bot_token = bot_token
        self.agent_name = agent_name
        self.router = router
        self.channel_ids = channel_ids or []
        self.base_url = "https://slack.com/api"
        self.headers = {"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json"}
        self._running = False
        self._last_timestamps: dict[str, str] = {}
        self._task = None
        self._bot_user_id: str | None = None

    async def start(self):
        self._running = True
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{self.base_url}/auth.test", headers=self.headers)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    self._bot_user_id = data.get("user_id")
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"Slack connector started for agent '{self.agent_name}'")

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
                    await asyncio.sleep(3)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Slack poll error: {e}")
                    await asyncio.sleep(5)

    async def _check_channel(self, client: httpx.AsyncClient, channel_id: str):
        params = {"channel": channel_id, "limit": 10}
        oldest = self._last_timestamps.get(channel_id)
        if oldest:
            params["oldest"] = oldest

        resp = await client.get(f"{self.base_url}/conversations.history",
                                headers=self.headers, params=params)
        if resp.status_code != 200:
            return
        data = resp.json()
        if not data.get("ok"):
            return

        messages = data.get("messages", [])
        if messages:
            self._last_timestamps[channel_id] = messages[0].get("ts", "")

        for msg in reversed(messages):
            if msg.get("user") == self._bot_user_id or msg.get("bot_id"):
                continue
            await self._handle_message(client, channel_id, msg)

    async def _handle_message(self, client: httpx.AsyncClient, channel_id: str, msg: dict):
        incoming = IncomingMessage(
            id=msg.get("ts", ""),
            from_id=msg.get("user", ""),
            from_name=msg.get("user", "User"),
            platform="slack",
            channel_id=channel_id,
            channel_name=f"slack-{channel_id}",
            text=msg.get("text", ""),
            raw=msg,
        )
        response = await self.router.route_message(self.agent_name, incoming)
        if response:
            await self._send_response(client, channel_id, response, msg.get("ts"))

    async def _send_response(self, client: httpx.AsyncClient, channel_id: str,
                             response: OutgoingMessage, thread_ts: str | None = None):
        if response.passthrough and response.passthrough_platform == "slack":
            payload = response.passthrough_payload
            payload["channel"] = channel_id
            await client.post(f"{self.base_url}/chat.postMessage",
                              headers=self.headers, json=payload)
            return

        payload: dict = {"channel": channel_id}
        if thread_ts:
            payload["thread_ts"] = thread_ts

        blocks = []
        if response.content:
            blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": response.content}})

        if response.buttons:
            blocks.append({
                "type": "actions",
                "elements": [
                    {"type": "button", "text": {"type": "plain_text", "text": b["label"]},
                     "action_id": b.get("action", b["label"])}
                    for b in response.buttons[:5]
                ]
            })

        if response.images:
            for img in response.images:
                if img.startswith("http"):
                    blocks.append({"type": "image", "image_url": img, "alt_text": "image"})

        if blocks:
            payload["blocks"] = blocks
            payload["text"] = response.content or ""  # fallback text
        else:
            payload["text"] = response.content or ""

        await client.post(f"{self.base_url}/chat.postMessage",
                          headers=self.headers, json=payload)
