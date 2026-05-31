"""Discord channel adapter.

Connects to Discord via bot token and polls channels for new messages,
emitting them as channel-hub messages through the message router.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from tinyagentos.channel_hub.message import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"
# Discord rate limit: 5 requests per 5 seconds per channel
_RATE_LIMIT_WINDOW = 5.0
_RATE_LIMIT_MAX = 5


class DiscordConnector:
    """Connector that polls Discord channels via HTTP API and routes messages
    through the channel-hub message router.

    Uses Discord's Get Channel Messages endpoint with after-id pagination
    to catch new messages. Respects Discord's rate limits (5 requests per
    5 seconds per channel).

    Filters:
      channel_ids: Only poll these channel IDs. If empty/unset, polls none.
    """

    def __init__(
        self,
        bot_token: str,
        agent_name: str,
        router,
        channel_ids: list[str] | None = None,
    ):
        self.bot_token = bot_token
        self.agent_name = agent_name
        self.router = router
        self.channel_ids = channel_ids or []
        self.headers = {"Authorization": f"Bot {bot_token}"}
        self._running = False
        self._task: asyncio.Task | None = None
        self._bot_user_id: str | None = None
        # Track last-seen message ID per channel for polling
        self._last_message_ids: dict[str, str] = {}
        # Per-channel rate limiting: 5 requests per 5-second window
        self._channel_sem: dict[str, asyncio.Semaphore] = {}

    async def start(self) -> None:
        """Start the Discord polling loop.

        Fetches the bot's user ID so we can filter out our own messages,
        then begins the poll loop.
        """
        self._running = True
        # Resolve bot user ID
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{DISCORD_API_BASE}/users/@me", headers=self.headers,
                )
                if resp.status_code == 200:
                    self._bot_user_id = resp.json().get("id")
        except Exception as exc:
            logger.warning("Could not resolve bot user ID: %s", exc)

        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            "Discord connector started for agent '%s', %d channel(s)",
            self.agent_name, len(self.channel_ids),
        )

    async def stop(self) -> None:
        """Stop the polling loop and cancel the background task."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            self._task = None

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        """Poll Discord channels every 2 seconds."""
        async with httpx.AsyncClient(timeout=15) as client:
            while self._running:
                try:
                    for channel_id in self.channel_ids:
                        await self._check_channel(client, channel_id)
                    await asyncio.sleep(2)
                except asyncio.CancelledError:
                    break
                except Exception as exc:
                    logger.error("Discord poll error: %s", exc)
                    await asyncio.sleep(5)

    async def _check_channel(
        self, client: httpx.AsyncClient, channel_id: str,
    ) -> None:
        """Fetch new messages from a single Discord channel.

        Uses after-id pagination so we only see messages newer than the
        last one we processed.  Respects per-channel rate limits via a
        semaphore (5 concurrent requests per 5-second window).
        """
        if channel_id not in self._channel_sem:
            self._channel_sem[channel_id] = asyncio.Semaphore(_RATE_LIMIT_MAX)

        async with self._channel_sem[channel_id]:
            params: dict[str, str | int] = {"limit": 10}
            last_id = self._last_message_ids.get(channel_id)
            if last_id:
                params["after"] = last_id

            try:
                resp = await client.get(
                    f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
                    headers=self.headers,
                    params=params,
                )
            except httpx.RequestError as exc:
                logger.error(
                    "Discord HTTP error on channel %s: %s", channel_id, exc,
                )
                return

            if resp.status_code == 429:
                retry_after = float(
                    resp.json().get("retry_after", _RATE_LIMIT_WINDOW),
                )
                logger.warning(
                    "Discord rate limited on channel %s, waiting %.1fs",
                    channel_id, retry_after,
                )
                await asyncio.sleep(retry_after)
                return

            if resp.status_code == 401:
                logger.error(
                    "Discord auth failure on channel %s — bad token?", channel_id,
                )
                return

            if resp.status_code != 200:
                logger.debug(
                    "Discord channel %s returned %d", channel_id, resp.status_code,
                )
                return

            messages = resp.json()
            if not messages:
                return

            # Update last-seen ID (API returns newest first)
            self._last_message_ids[channel_id] = messages[0]["id"]

            # Process in chronological order (oldest first)
            for msg in reversed(messages):
                if msg.get("author", {}).get("id") == self._bot_user_id:
                    continue  # Skip our own messages
                await self._handle_message(client, channel_id, msg)

    # ------------------------------------------------------------------
    # Message handling
    # ------------------------------------------------------------------

    async def _handle_message(
        self,
        client: httpx.AsyncClient,
        channel_id: str,
        msg: dict,
    ) -> None:
        """Route a single Discord message through the message router."""
        guild_id = msg.get("guild_id", "")
        author = msg.get("author", {})

        incoming = IncomingMessage(
            id=msg["id"],
            from_id=author.get("id", ""),
            from_name=author.get("username") or author.get("global_name", "User"),
            platform="discord",
            channel_id=channel_id,
            channel_name=self._build_channel_name(guild_id, channel_id),
            text=msg.get("content", ""),
            raw={
                "source": "discord",
                "channel_id": channel_id,
                "guild_id": guild_id,
                "author": {
                    "id": author.get("id"),
                    "username": author.get("username"),
                    "global_name": author.get("global_name"),
                },
                "payload": msg,
            },
        )

        response = await self.router.route_message(self.agent_name, incoming)
        if response is not None:
            await self._send_response(client, channel_id, response)

    async def _send_response(
        self,
        client: httpx.AsyncClient,
        channel_id: str,
        response: OutgoingMessage,
    ) -> None:
        """Send a response back to a Discord channel.

        Supports passthrough payloads, content, embeds (images), and
        interactive components (buttons).
        """
        if response.passthrough and response.passthrough_platform == "discord":
            payload = response.passthrough_payload
            await client.post(
                f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
                headers=self.headers,
                json=payload,
            )
            return

        payload: dict[str, object] = {}

        if response.content:
            payload["content"] = response.content

        if response.images:
            payload["embeds"] = [
                {"image": {"url": img}}
                for img in response.images
                if img.startswith("http")
            ]

        if response.buttons:
            payload["components"] = [
                {
                    "type": 1,  # ACTION_ROW
                    "components": [
                        {
                            "type": 2,   # BUTTON
                            "style": 1,  # PRIMARY
                            "label": b["label"],
                            "custom_id": b.get("action", b["label"]),
                        }
                        for b in response.buttons[:5]  # Discord max 5 per row
                    ],
                },
            ]

        if payload:
            try:
                await client.post(
                    f"{DISCORD_API_BASE}/channels/{channel_id}/messages",
                    headers=self.headers,
                    json=payload,
                )
            except httpx.RequestError as exc:
                logger.error(
                    "Failed to send Discord response to %s: %s", channel_id, exc,
                )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_channel_name(guild_id: str, channel_id: str) -> str:
        """Build a human-readable channel name for logging."""
        if guild_id:
            return f"discord:{guild_id}:{channel_id}"
        return f"discord:dm:{channel_id}"
