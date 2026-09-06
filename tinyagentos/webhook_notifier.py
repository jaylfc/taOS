from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class WebhookNotifier:
    """Send notifications to external services (Slack, Discord, Telegram, generic webhook)."""

    def __init__(self, config: dict):
        self.webhooks: list[dict] = config.get("webhooks", [])

    async def notify(self, title: str, message: str, level: str = "info") -> None:
        """Send a notification to all configured webhooks."""
        if not self.webhooks:
            return
        for wh in self.webhooks:
            try:
                await self._send(wh, title, message, level)
            except Exception as e:
                logger.warning(f"Webhook failed ({wh.get('type', 'generic')}): {e}")

    async def _send(self, webhook: dict, title: str, message: str, level: str) -> None:
        url = webhook["url"]
        wh_type = webhook.get("type", "generic")
        async with httpx.AsyncClient(timeout=10) as client:
            if wh_type == "slack":
                await client.post(url, json={
                    "text": f"*{title}*\n{message}",
                })
            elif wh_type == "discord":
                color = {"info": 3066993, "warning": 16776960, "error": 15158332}.get(level, 3066993)
                await client.post(url, json={
                    "embeds": [{"title": title, "description": message, "color": color}],
                })
            elif wh_type == "telegram":
                bot_token = webhook.get("bot_token", "")
                chat_id = webhook.get("chat_id", "")
                await client.post(
                    f"https://api.telegram.org/bot{bot_token}/sendMessage",
                    json={
                        "chat_id": chat_id,
                        "text": f"*{title}*\n{message}",
                        "parse_mode": "Markdown",
                    },
                )
            else:  # generic
                await client.post(url, json={
                    "title": title,
                    "message": message,
                    "level": level,
                })
