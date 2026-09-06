from __future__ import annotations
import asyncio
import logging
import httpx
from tinyagentos.channel_hub.message import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class TelegramConnector:
    def __init__(self, bot_token: str, agent_name: str, router):
        self.bot_token = bot_token
        self.agent_name = agent_name
        self.router = router
        self.base_url = f"https://api.telegram.org/bot{bot_token}"
        self._running = False
        self._offset = 0
        self._task = None

    async def start(self):
        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(f"Telegram connector started for agent '{self.agent_name}'")

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    async def _poll_loop(self):
        async with httpx.AsyncClient(timeout=30) as client:
            while self._running:
                try:
                    resp = await client.get(f"{self.base_url}/getUpdates", params={
                        "offset": self._offset, "timeout": 20,
                    })
                    if resp.status_code == 200:
                        updates = resp.json().get("result", [])
                        for update in updates:
                            self._offset = update["update_id"] + 1
                            await self._handle_update(client, update)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Telegram poll error: {e}")
                    await asyncio.sleep(5)

    async def _handle_update(self, client: httpx.AsyncClient, update: dict):
        msg = update.get("message") or update.get("callback_query", {}).get("message")
        if not msg:
            return

        text = msg.get("text", "")
        callback_data = update.get("callback_query", {}).get("data")
        if callback_data:
            text = f"[callback:{callback_data}]"

        incoming = IncomingMessage(
            id=str(msg.get("message_id", "")),
            from_id=str(msg.get("from", {}).get("id", "")),
            from_name=msg.get("from", {}).get("first_name", "User"),
            platform="telegram",
            channel_id=str(msg.get("chat", {}).get("id", "")),
            channel_name=msg.get("chat", {}).get("title", "DM"),
            text=text,
            raw=update,
        )

        response = await self.router.route_message(self.agent_name, incoming)
        if response:
            await self._send_response(client, msg["chat"]["id"], response)

    async def _send_response(self, client: httpx.AsyncClient, chat_id: int, response: OutgoingMessage):
        if response.passthrough and response.passthrough_platform == "telegram":
            # Passthrough — send raw Telegram API call
            payload = response.passthrough_payload
            method = payload.pop("method", "sendMessage")
            payload["chat_id"] = chat_id
            await client.post(f"{self.base_url}/{method}", json=payload)
            return

        # Send text with markdown
        if response.content:
            payload = {
                "chat_id": chat_id,
                "text": response.content,
                "parse_mode": "Markdown",
            }
            # Add inline keyboard if buttons
            if response.buttons:
                payload["reply_markup"] = {
                    "inline_keyboard": [
                        [{"text": b["label"], "callback_data": b.get("action", b["label"])}]
                        for b in response.buttons
                    ]
                }
            await client.post(f"{self.base_url}/sendMessage", json=payload)

        # Send images
        for image_path in response.images:
            if image_path.startswith("http"):
                await client.post(f"{self.base_url}/sendPhoto", json={
                    "chat_id": chat_id, "photo": image_path,
                })
            else:
                # Local file — send as multipart
                try:
                    with open(image_path, "rb") as f:
                        await client.post(f"{self.base_url}/sendPhoto",
                            data={"chat_id": str(chat_id)},
                            files={"photo": f})
                except FileNotFoundError:
                    pass
