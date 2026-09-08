"""Tests for Discord 429 rate-limit handling and Slack at-least-once cursor delivery."""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.channel_hub.discord_connector import DiscordConnector
from tinyagentos.channel_hub.slack_connector import SlackConnector


class TestDiscordRateLimit:
    @pytest.mark.asyncio
    async def test_429_arms_rate_limit_and_skips_handle(self):
        connector = DiscordConnector("token", "agent", MagicMock(), channel_ids=["ch1"])
        client = AsyncMock()
        resp = MagicMock()
        resp.status_code = 429
        resp.headers = {"Retry-After": "3"}
        resp.json.return_value = []
        client.get.return_value = resp

        with patch.object(connector, "_handle_message") as mock_handle:
            before = time.time()
            await connector._check_channel(client, "ch1")
            after = time.time()

        assert "ch1" in connector._last_rate_limit
        deadline = connector._last_rate_limit["ch1"]
        assert before + 3 <= deadline <= after + 3
        mock_handle.assert_not_called()

    @pytest.mark.asyncio
    async def test_poll_loop_skips_rate_limited_channel(self):
        connector = DiscordConnector("token", "agent", MagicMock(), channel_ids=["ch1"])
        connector._last_rate_limit["ch1"] = time.time() + 9999

        mock_client = AsyncMock()

        async def stop_after_first_sleep(seconds):
            connector._running = False

        with patch("tinyagentos.channel_hub.discord_connector.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch("asyncio.sleep", side_effect=stop_after_first_sleep):
                await connector._poll_loop()

        mock_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_200_empty_does_not_arm_rate_limit(self):
        connector = DiscordConnector("token", "agent", MagicMock(), channel_ids=["ch1"])
        client = AsyncMock()
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = []
        client.get.return_value = resp

        await connector._check_channel(client, "ch1")

        assert "ch1" not in connector._last_rate_limit


class TestSlackCursorNotAdvancedOnRaise:
    @pytest.mark.asyncio
    async def test_handle_message_raise_preserves_cursor(self):
        connector = SlackConnector("token", "agent", MagicMock(), channel_ids=["ch1"])
        connector._last_timestamps["ch1"] = "old_ts"
        client = AsyncMock()

        messages = [
            {"ts": "1.0", "text": "first", "user": "U1"},
            {"ts": "2.0", "text": "second", "user": "U2"},
        ]
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"ok": True, "messages": messages}
        client.get.return_value = resp

        with patch.object(connector, "_handle_message", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                await connector._check_channel(client, "ch1")

        assert connector._last_timestamps["ch1"] == "old_ts"
