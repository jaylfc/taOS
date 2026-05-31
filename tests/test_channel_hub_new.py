"""Tests for new Channel Hub connectors and framework adapters."""
from __future__ import annotations

import asyncio
import email
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.channel_hub.message import IncomingMessage, OutgoingMessage


# ---------------------------------------------------------------------------
# Discord Connector
# ---------------------------------------------------------------------------

class TestDiscordConnector:
    @pytest.mark.asyncio
    async def test_send_text_response(self):
        from tinyagentos.channel_hub.discord_connector import DiscordConnector

        router = MagicMock()
        connector = DiscordConnector(
            bot_token="fake_token", agent_name="test", router=router, channel_ids=["ch1"],
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        response = OutgoingMessage(content="Hello from Discord!")
        await connector._send_response(mock_client, "ch1", response)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "/channels/ch1/messages" in call_args[0][0]
        assert call_args[1]["json"]["content"] == "Hello from Discord!"

    @pytest.mark.asyncio
    async def test_send_response_with_buttons(self):
        from tinyagentos.channel_hub.discord_connector import DiscordConnector

        router = MagicMock()
        connector = DiscordConnector(
            bot_token="fake_token", agent_name="test", router=router,
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        response = OutgoingMessage(
            content="Pick one:",
            buttons=[{"label": "A", "action": "pick_a"}, {"label": "B", "action": "pick_b"}],
        )
        await connector._send_response(mock_client, "ch1", response)

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert "components" in payload
        components = payload["components"][0]["components"]
        assert len(components) == 2
        assert components[0]["label"] == "A"
        assert components[0]["custom_id"] == "pick_a"

    @pytest.mark.asyncio
    async def test_send_response_with_images(self):
        from tinyagentos.channel_hub.discord_connector import DiscordConnector

        router = MagicMock()
        connector = DiscordConnector(
            bot_token="fake_token", agent_name="test", router=router,
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        response = OutgoingMessage(
            content="Check this out",
            images=["http://example.com/img.png"],
        )
        await connector._send_response(mock_client, "ch1", response)

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert "embeds" in payload
        assert payload["embeds"][0]["image"]["url"] == "http://example.com/img.png"

    @pytest.mark.asyncio
    async def test_send_passthrough(self):
        from tinyagentos.channel_hub.discord_connector import DiscordConnector

        router = MagicMock()
        connector = DiscordConnector(
            bot_token="fake_token", agent_name="test", router=router,
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        response = OutgoingMessage(
            passthrough=True,
            passthrough_platform="discord",
            passthrough_payload={"content": "raw payload", "tts": True},
        )
        await connector._send_response(mock_client, "ch1", response)

        call_args = mock_client.post.call_args
        assert "/channels/ch1/messages" in call_args[0][0]
        assert call_args[1]["json"]["tts"] is True

    @pytest.mark.asyncio
    async def test_handle_message_routes(self):
        from tinyagentos.channel_hub.discord_connector import DiscordConnector

        mock_router = AsyncMock()
        mock_router.route_message = AsyncMock(return_value=None)
        connector = DiscordConnector(
            bot_token="fake", agent_name="bot1", router=mock_router, channel_ids=["ch1"],
        )

        mock_client = AsyncMock()
        msg = {
            "id": "msg123",
            "author": {"id": "user1", "username": "Jay"},
            "content": "hello discord",
        }
        await connector._handle_message(mock_client, "ch1", msg)

        mock_router.route_message.assert_called_once()
        incoming = mock_router.route_message.call_args[0][1]
        assert incoming.text == "hello discord"
        assert incoming.from_name == "Jay"
        assert incoming.platform == "discord"
        assert incoming.channel_id == "ch1"

    @pytest.mark.asyncio
    async def test_check_channel_skips_own_messages(self):
        from tinyagentos.channel_hub.discord_connector import DiscordConnector

        mock_router = AsyncMock()
        mock_router.route_message = AsyncMock(return_value=None)
        connector = DiscordConnector(
            bot_token="fake", agent_name="bot1", router=mock_router, channel_ids=["ch1"],
        )
        connector._bot_user_id = "bot_id_123"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"id": "m2", "author": {"id": "bot_id_123", "username": "Bot"}, "content": "I said this"},
            {"id": "m1", "author": {"id": "user1", "username": "Jay"}, "content": "hey"},
        ]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        await connector._check_channel(mock_client, "ch1")

        # Only the user message should be routed, not the bot's own message
        assert mock_router.route_message.call_count == 1
        incoming = mock_router.route_message.call_args[0][1]
        assert incoming.text == "hey"

    @pytest.mark.asyncio
    async def test_check_channel_updates_last_id(self):
        from tinyagentos.channel_hub.discord_connector import DiscordConnector

        mock_router = AsyncMock()
        mock_router.route_message = AsyncMock(return_value=None)
        connector = DiscordConnector(
            bot_token="fake", agent_name="bot1", router=mock_router, channel_ids=["ch1"],
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"id": "m5", "author": {"id": "u1", "username": "A"}, "content": "newest"},
            {"id": "m4", "author": {"id": "u2", "username": "B"}, "content": "older"},
        ]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        await connector._check_channel(mock_client, "ch1")
        assert connector._last_message_ids["ch1"] == "m5"

    @pytest.mark.asyncio
    async def test_check_channel_non_200(self):
        from tinyagentos.channel_hub.discord_connector import DiscordConnector

        mock_router = AsyncMock()
        mock_router.route_message = AsyncMock(return_value=None)
        connector = DiscordConnector(
            bot_token="fake", agent_name="bot1", router=mock_router, channel_ids=["ch1"],
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        await connector._check_channel(mock_client, "ch1")
        mock_router.route_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        from tinyagentos.channel_hub.discord_connector import DiscordConnector

        router = MagicMock()
        connector = DiscordConnector(
            bot_token="fake", agent_name="test", router=router, channel_ids=["ch1"],
        )

        with patch.object(connector, "_poll_loop", new_callable=AsyncMock):
            # Mock the start GET for bot user ID
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"id": "bot123"}

            with patch("tinyagentos.channel_hub.discord_connector.httpx.AsyncClient") as mock_httpx:
                mock_client_instance = AsyncMock()
                mock_client_instance.get = AsyncMock(return_value=mock_resp)
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock(return_value=False)
                mock_httpx.return_value = mock_client_instance

                await connector.start()
                assert connector._running is True
                assert connector._bot_user_id == "bot123"

                await connector.stop()
                assert connector._running is False

    @pytest.mark.asyncio
    async def test_buttons_capped_at_five(self):
        from tinyagentos.channel_hub.discord_connector import DiscordConnector

        router = MagicMock()
        connector = DiscordConnector(
            bot_token="fake_token", agent_name="test", router=router,
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        # 7 buttons — Discord max is 5 per row
        buttons = [{"label": f"B{i}", "action": f"a{i}"} for i in range(7)]
        response = OutgoingMessage(content="Pick:", buttons=buttons)
        await connector._send_response(mock_client, "ch1", response)

        call_args = mock_client.post.call_args
        components = call_args[1]["json"]["components"][0]["components"]
        assert len(components) == 5

    @pytest.mark.asyncio
    async def test_empty_response_not_sent(self):
        from tinyagentos.channel_hub.discord_connector import DiscordConnector

        router = MagicMock()
        connector = DiscordConnector(
            bot_token="fake_token", agent_name="test", router=router,
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        response = OutgoingMessage()  # empty
        await connector._send_response(mock_client, "ch1", response)

        mock_client.post.assert_not_called()


# ---------------------------------------------------------------------------
# Discord Adapter (channel_hub/adapters/discord.py)
# Tests for the reviewed findings: start() raises on missing bot ID + no semaphore.
# ---------------------------------------------------------------------------

class TestDiscordAdapter:
    @pytest.mark.asyncio
    async def test_start_raises_on_network_error(self):
        """start() raises RuntimeError when the /users/@me request fails."""
        from tinyagentos.channel_hub.adapters.discord import DiscordConnector

        router = MagicMock()
        connector = DiscordConnector(
            bot_token="bad_token", agent_name="test", router=router, channel_ids=["ch1"],
        )

        with patch("tinyagentos.channel_hub.adapters.discord.httpx.AsyncClient") as mock_httpx:
            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(side_effect=Exception("network error"))
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value = mock_client_instance

            with pytest.raises(RuntimeError, match="could not resolve bot user ID"):
                await connector.start()

    @pytest.mark.asyncio
    async def test_start_raises_on_empty_user_id(self):
        """start() raises RuntimeError when /users/@me returns no id."""
        from tinyagentos.channel_hub.adapters.discord import DiscordConnector

        router = MagicMock()
        connector = DiscordConnector(
            bot_token="bad_token", agent_name="test", router=router, channel_ids=["ch1"],
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {}  # no "id" field

        with patch("tinyagentos.channel_hub.adapters.discord.httpx.AsyncClient") as mock_httpx:
            mock_client_instance = AsyncMock()
            mock_client_instance.get = AsyncMock(return_value=mock_resp)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value = mock_client_instance

            with pytest.raises(RuntimeError, match="empty user ID"):
                await connector.start()

    @pytest.mark.asyncio
    async def test_start_succeeds_with_valid_token(self):
        """start() sets _bot_user_id and starts the poll task when token is valid."""
        from tinyagentos.channel_hub.adapters.discord import DiscordConnector

        router = MagicMock()
        connector = DiscordConnector(
            bot_token="good_token", agent_name="test", router=router, channel_ids=["ch1"],
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"id": "bot_id_42"}

        with patch.object(connector, "_poll_loop", new_callable=AsyncMock):
            with patch("tinyagentos.channel_hub.adapters.discord.httpx.AsyncClient") as mock_httpx:
                mock_client_instance = AsyncMock()
                mock_client_instance.get = AsyncMock(return_value=mock_resp)
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock(return_value=False)
                mock_httpx.return_value = mock_client_instance

                await connector.start()
                assert connector._bot_user_id == "bot_id_42"
                assert connector._running is True

                await connector.stop()
                assert connector._running is False

    @pytest.mark.asyncio
    async def test_check_channel_no_semaphore(self):
        """_check_channel works without any semaphore — channels are polled sequentially."""
        from tinyagentos.channel_hub.adapters.discord import DiscordConnector

        mock_router = AsyncMock()
        mock_router.route_message = AsyncMock(return_value=None)
        connector = DiscordConnector(
            bot_token="fake", agent_name="bot1", router=mock_router, channel_ids=["ch1"],
        )
        connector._bot_user_id = "bot_id_99"

        assert not hasattr(connector, "_channel_sem"), \
            "_channel_sem must not exist on the adapter"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [
            {"id": "m1", "author": {"id": "user1", "username": "Jay"}, "content": "hello"},
        ]

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        await connector._check_channel(mock_client, "ch1")
        assert mock_router.route_message.call_count == 1


# ---------------------------------------------------------------------------
# WebChat Connector
# ---------------------------------------------------------------------------

class TestWebChatConnector:
    @pytest.mark.asyncio
    async def test_handle_websocket_message(self):
        from tinyagentos.channel_hub.webchat_connector import WebChatConnector

        mock_router = AsyncMock()
        response = OutgoingMessage(content="Hello back!")
        mock_router.route_message = AsyncMock(return_value=response)

        connector = WebChatConnector(agent_name="test-agent", router=mock_router)

        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.receive_text = AsyncMock(
            side_effect=[
                json.dumps({"name": "Jay", "text": "hello"}),
                Exception("disconnect"),  # simulate end
            ]
        )
        mock_ws.send_text = AsyncMock()

        await connector.handle_websocket(mock_ws)

        mock_ws.accept.assert_called_once()
        mock_router.route_message.assert_called_once()
        incoming = mock_router.route_message.call_args[0][1]
        assert incoming.text == "hello"
        assert incoming.from_name == "Jay"
        assert incoming.platform == "web"

        mock_ws.send_text.assert_called_once()
        sent_data = json.loads(mock_ws.send_text.call_args[0][0])
        assert sent_data["content"] == "Hello back!"

    @pytest.mark.asyncio
    async def test_handle_websocket_no_response(self):
        from tinyagentos.channel_hub.webchat_connector import WebChatConnector

        mock_router = AsyncMock()
        mock_router.route_message = AsyncMock(return_value=None)

        connector = WebChatConnector(agent_name="test-agent", router=mock_router)

        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.receive_text = AsyncMock(
            side_effect=[
                json.dumps({"text": "hello"}),
                Exception("done"),
            ]
        )
        mock_ws.send_text = AsyncMock()

        await connector.handle_websocket(mock_ws)

        # No response sent when route returns None
        mock_ws.send_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_connection_cleanup(self):
        from tinyagentos.channel_hub.webchat_connector import WebChatConnector
        from fastapi import WebSocketDisconnect

        mock_router = AsyncMock()
        connector = WebChatConnector(agent_name="test-agent", router=mock_router)

        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.receive_text = AsyncMock(side_effect=WebSocketDisconnect())

        await connector.handle_websocket(mock_ws)

        # After disconnect, connections dict should be empty
        assert len(connector._connections) == 0

    @pytest.mark.asyncio
    async def test_response_includes_buttons_and_images(self):
        from tinyagentos.channel_hub.webchat_connector import WebChatConnector

        response = OutgoingMessage(
            content="Here you go",
            buttons=[{"label": "OK", "action": "ok"}],
            images=["http://example.com/img.png"],
        )
        mock_router = AsyncMock()
        mock_router.route_message = AsyncMock(return_value=response)

        connector = WebChatConnector(agent_name="test-agent", router=mock_router)

        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        mock_ws.receive_text = AsyncMock(
            side_effect=[json.dumps({"text": "show"}), Exception("done")]
        )
        mock_ws.send_text = AsyncMock()

        await connector.handle_websocket(mock_ws)

        sent_data = json.loads(mock_ws.send_text.call_args[0][0])
        assert sent_data["content"] == "Here you go"
        assert len(sent_data["buttons"]) == 1
        assert len(sent_data["images"]) == 1
        assert "timestamp" in sent_data


# ---------------------------------------------------------------------------
# Framework Adapter health endpoints (import and structure tests)
# ---------------------------------------------------------------------------

class TestAdapterStructure:
    def test_pocketflow_adapter_has_health(self):
        """PocketFlow adapter module has /health endpoint."""
        from tinyagentos.adapters.pocketflow_adapter import app
        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/message" in routes

    def test_openclaw_adapter_has_health(self):
        """OpenClaw adapter module has /health endpoint."""
        from tinyagentos.adapters.openclaw_adapter import app
        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/message" in routes

    def test_langroid_adapter_has_health(self):
        """Langroid adapter module has /health endpoint."""
        from tinyagentos.adapters.langroid_adapter import app
        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/message" in routes

    def test_generic_adapter_has_health(self):
        """Verify existing generic adapter still works."""
        from tinyagentos.adapters.generic_adapter import app
        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/message" in routes

    def test_smolagents_adapter_has_health(self):
        """Verify existing smolagents adapter still works."""
        from tinyagentos.adapters.smolagents_adapter import app
        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/message" in routes


# ---------------------------------------------------------------------------
# Channel Hub API — Discord and WebChat connect endpoints
# ---------------------------------------------------------------------------

class TestChannelHubAPINew:
    @pytest.mark.asyncio
    async def test_connect_discord_secret_not_found(self, client):
        resp = await client.post("/api/channel-hub/connect", json={
            "platform": "discord",
            "bot_token_secret": "nonexistent_discord_secret",
            "agent_name": "test-agent",
        })
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_connect_webchat_no_agent_name(self, client):
        resp = await client.post("/api/channel-hub/connect", json={
            "platform": "webchat",
        })
        assert resp.status_code == 400
        assert "agent_name" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_connect_webchat_success(self, client):
        resp = await client.post("/api/channel-hub/connect", json={
            "platform": "webchat",
            "agent_name": "chat-agent",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "connected"
        assert data["platform"] == "webchat"
        assert data["agent_name"] == "chat-agent"

    @pytest.mark.asyncio
    async def test_disconnect_webchat(self, client):
        # Connect first
        await client.post("/api/channel-hub/connect", json={
            "platform": "webchat",
            "agent_name": "temp-agent",
        })
        # Disconnect
        resp = await client.post("/api/channel-hub/disconnect", json={
            "platform": "webchat",
            "agent_name": "temp-agent",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "disconnected"

    @pytest.mark.asyncio
    async def test_status_shows_webchat_connector(self, client):
        await client.post("/api/channel-hub/connect", json={
            "platform": "webchat",
            "agent_name": "status-agent",
        })
        resp = await client.get("/api/channel-hub/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "webchat:status-agent" in data["connectors"]
        connector_info = data["connectors"]["webchat:status-agent"]
        assert connector_info["platform"] == "webchat"

    @pytest.mark.asyncio
    async def test_connect_unsupported_platform_still_fails(self, client):
        await client.post("/api/secrets", json={"name": "irc_token", "value": "fake"})
        resp = await client.post("/api/channel-hub/connect", json={
            "platform": "irc",
            "bot_token_secret": "irc_token",
            "agent_name": "test-agent",
        })
        assert resp.status_code == 400
        assert "not yet supported" in resp.json()["error"]


# ---------------------------------------------------------------------------
# Slack Connector
# ---------------------------------------------------------------------------

class TestSlackConnector:
    @pytest.mark.asyncio
    async def test_send_text_response(self):
        from tinyagentos.channel_hub.slack_connector import SlackConnector

        router = MagicMock()
        connector = SlackConnector(
            bot_token="xoxb-fake", agent_name="test", router=router, channel_ids=["C123"],
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        response = OutgoingMessage(content="Hello from Slack!")
        await connector._send_response(mock_client, "C123", response)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "chat.postMessage" in call_args[0][0]
        payload = call_args[1]["json"]
        assert payload["channel"] == "C123"
        assert payload["text"] == "Hello from Slack!"
        assert any(b["type"] == "section" for b in payload["blocks"])

    @pytest.mark.asyncio
    async def test_send_response_with_buttons(self):
        from tinyagentos.channel_hub.slack_connector import SlackConnector

        router = MagicMock()
        connector = SlackConnector(
            bot_token="xoxb-fake", agent_name="test", router=router,
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        response = OutgoingMessage(
            content="Pick one:",
            buttons=[{"label": "A", "action": "pick_a"}, {"label": "B", "action": "pick_b"}],
        )
        await connector._send_response(mock_client, "C123", response)

        payload = mock_client.post.call_args[1]["json"]
        actions_block = [b for b in payload["blocks"] if b["type"] == "actions"]
        assert len(actions_block) == 1
        elements = actions_block[0]["elements"]
        assert len(elements) == 2
        assert elements[0]["text"]["text"] == "A"
        assert elements[0]["action_id"] == "pick_a"

    @pytest.mark.asyncio
    async def test_send_response_with_images(self):
        from tinyagentos.channel_hub.slack_connector import SlackConnector

        router = MagicMock()
        connector = SlackConnector(
            bot_token="xoxb-fake", agent_name="test", router=router,
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        response = OutgoingMessage(
            content="Check this out",
            images=["http://example.com/img.png"],
        )
        await connector._send_response(mock_client, "C123", response)

        payload = mock_client.post.call_args[1]["json"]
        image_blocks = [b for b in payload["blocks"] if b["type"] == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["image_url"] == "http://example.com/img.png"

    @pytest.mark.asyncio
    async def test_send_passthrough(self):
        from tinyagentos.channel_hub.slack_connector import SlackConnector

        router = MagicMock()
        connector = SlackConnector(
            bot_token="xoxb-fake", agent_name="test", router=router,
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        response = OutgoingMessage(
            passthrough=True,
            passthrough_platform="slack",
            passthrough_payload={"text": "raw payload", "unfurl_links": True},
        )
        await connector._send_response(mock_client, "C123", response)

        payload = mock_client.post.call_args[1]["json"]
        assert payload["channel"] == "C123"
        assert payload["unfurl_links"] is True

    @pytest.mark.asyncio
    async def test_send_response_with_thread_ts(self):
        from tinyagentos.channel_hub.slack_connector import SlackConnector

        router = MagicMock()
        connector = SlackConnector(
            bot_token="xoxb-fake", agent_name="test", router=router,
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        response = OutgoingMessage(content="Threaded reply")
        await connector._send_response(mock_client, "C123", response, thread_ts="1234567890.123456")

        payload = mock_client.post.call_args[1]["json"]
        assert payload["thread_ts"] == "1234567890.123456"

    @pytest.mark.asyncio
    async def test_handle_message_routes(self):
        from tinyagentos.channel_hub.slack_connector import SlackConnector

        mock_router = AsyncMock()
        mock_router.route_message = AsyncMock(return_value=None)
        connector = SlackConnector(
            bot_token="xoxb-fake", agent_name="bot1", router=mock_router, channel_ids=["C123"],
        )

        mock_client = AsyncMock()
        msg = {"ts": "123.456", "user": "U123", "text": "hello slack"}
        await connector._handle_message(mock_client, "C123", msg)

        mock_router.route_message.assert_called_once()
        incoming = mock_router.route_message.call_args[0][1]
        assert incoming.text == "hello slack"
        assert incoming.platform == "slack"
        assert incoming.channel_id == "C123"

    @pytest.mark.asyncio
    async def test_check_channel_skips_bot_messages(self):
        from tinyagentos.channel_hub.slack_connector import SlackConnector

        mock_router = AsyncMock()
        mock_router.route_message = AsyncMock(return_value=None)
        connector = SlackConnector(
            bot_token="xoxb-fake", agent_name="bot1", router=mock_router, channel_ids=["C123"],
        )
        connector._bot_user_id = "U_BOT"

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "messages": [
                {"ts": "2.0", "user": "U_BOT", "text": "I said this"},
                {"ts": "1.0", "user": "U_HUMAN", "text": "hey"},
            ],
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        await connector._check_channel(mock_client, "C123")

        assert mock_router.route_message.call_count == 1
        incoming = mock_router.route_message.call_args[0][1]
        assert incoming.text == "hey"

    @pytest.mark.asyncio
    async def test_check_channel_skips_bot_id_messages(self):
        from tinyagentos.channel_hub.slack_connector import SlackConnector

        mock_router = AsyncMock()
        mock_router.route_message = AsyncMock(return_value=None)
        connector = SlackConnector(
            bot_token="xoxb-fake", agent_name="bot1", router=mock_router, channel_ids=["C123"],
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "messages": [
                {"ts": "2.0", "bot_id": "B123", "text": "bot msg"},
                {"ts": "1.0", "user": "U_HUMAN", "text": "human msg"},
            ],
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        await connector._check_channel(mock_client, "C123")

        assert mock_router.route_message.call_count == 1

    @pytest.mark.asyncio
    async def test_check_channel_updates_timestamp(self):
        from tinyagentos.channel_hub.slack_connector import SlackConnector

        mock_router = AsyncMock()
        mock_router.route_message = AsyncMock(return_value=None)
        connector = SlackConnector(
            bot_token="xoxb-fake", agent_name="bot1", router=mock_router, channel_ids=["C123"],
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "ok": True,
            "messages": [
                {"ts": "99.0", "user": "U1", "text": "newest"},
            ],
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        await connector._check_channel(mock_client, "C123")
        assert connector._last_timestamps["C123"] == "99.0"

    @pytest.mark.asyncio
    async def test_check_channel_non_200(self):
        from tinyagentos.channel_hub.slack_connector import SlackConnector

        mock_router = AsyncMock()
        mock_router.route_message = AsyncMock(return_value=None)
        connector = SlackConnector(
            bot_token="xoxb-fake", agent_name="bot1", router=mock_router, channel_ids=["C123"],
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        await connector._check_channel(mock_client, "C123")
        mock_router.route_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_channel_not_ok(self):
        from tinyagentos.channel_hub.slack_connector import SlackConnector

        mock_router = AsyncMock()
        mock_router.route_message = AsyncMock(return_value=None)
        connector = SlackConnector(
            bot_token="xoxb-fake", agent_name="bot1", router=mock_router, channel_ids=["C123"],
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"ok": False, "error": "channel_not_found"}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)

        await connector._check_channel(mock_client, "C123")
        mock_router.route_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_buttons_capped_at_five(self):
        from tinyagentos.channel_hub.slack_connector import SlackConnector

        router = MagicMock()
        connector = SlackConnector(
            bot_token="xoxb-fake", agent_name="test", router=router,
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        buttons = [{"label": f"B{i}", "action": f"a{i}"} for i in range(7)]
        response = OutgoingMessage(content="Pick:", buttons=buttons)
        await connector._send_response(mock_client, "C123", response)

        payload = mock_client.post.call_args[1]["json"]
        actions_block = [b for b in payload["blocks"] if b["type"] == "actions"]
        assert len(actions_block[0]["elements"]) == 5

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        from tinyagentos.channel_hub.slack_connector import SlackConnector

        router = MagicMock()
        connector = SlackConnector(
            bot_token="xoxb-fake", agent_name="test", router=router, channel_ids=["C123"],
        )

        with patch.object(connector, "_poll_loop", new_callable=AsyncMock):
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"ok": True, "user_id": "U_BOT"}

            with patch("tinyagentos.channel_hub.slack_connector.httpx.AsyncClient") as mock_httpx:
                mock_client_instance = AsyncMock()
                mock_client_instance.post = AsyncMock(return_value=mock_resp)
                mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
                mock_client_instance.__aexit__ = AsyncMock(return_value=False)
                mock_httpx.return_value = mock_client_instance

                await connector.start()
                assert connector._running is True
                assert connector._bot_user_id == "U_BOT"

                await connector.stop()
                assert connector._running is False

    @pytest.mark.asyncio
    async def test_empty_content_sends_empty_text(self):
        from tinyagentos.channel_hub.slack_connector import SlackConnector

        router = MagicMock()
        connector = SlackConnector(
            bot_token="xoxb-fake", agent_name="test", router=router,
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        response = OutgoingMessage()
        await connector._send_response(mock_client, "C123", response)

        payload = mock_client.post.call_args[1]["json"]
        assert payload["text"] == ""


# ---------------------------------------------------------------------------
# Email Connector
# ---------------------------------------------------------------------------

class TestEmailConnector:
    def test_process_email_plain_text(self):
        from tinyagentos.channel_hub.email_connector import EmailConnector

        mock_router = MagicMock()
        mock_router.route_message = AsyncMock(return_value=None)

        connector = EmailConnector(
            agent_name="test", router=mock_router,
            imap_host="imap.test.com", imap_port=993,
            smtp_host="smtp.test.com", smtp_port=587,
            username="bot@test.com", password="pass",
        )

        raw = (
            b"From: jay@example.com\r\n"
            b"Subject: Hello\r\n"
            b"Message-ID: <msg1@example.com>\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"How are you?"
        )
        msg = email.message_from_bytes(raw)
        connector._process_email(msg)

        mock_router.route_message.assert_called_once()
        incoming = mock_router.route_message.call_args[0][1]
        assert incoming.platform == "email"
        assert "Hello" in incoming.text
        assert "How are you?" in incoming.text
        assert incoming.from_id == "jay@example.com"

    def test_process_email_multipart(self):
        from tinyagentos.channel_hub.email_connector import EmailConnector

        mock_router = MagicMock()
        mock_router.route_message = AsyncMock(return_value=None)

        connector = EmailConnector(
            agent_name="test", router=mock_router,
            imap_host="imap.test.com", imap_port=993,
            smtp_host="smtp.test.com", smtp_port=587,
            username="bot@test.com", password="pass",
        )

        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        msg = MIMEMultipart()
        msg["From"] = "jay@example.com"
        msg["Subject"] = "Multipart Test"
        msg["Message-ID"] = "<msg2@example.com>"
        msg.attach(MIMEText("Plain text body", "plain"))
        msg.attach(MIMEText("<b>HTML body</b>", "html"))

        # Convert to bytes and back to simulate real email
        raw_msg = email.message_from_bytes(msg.as_bytes())
        connector._process_email(raw_msg)

        mock_router.route_message.assert_called_once()
        incoming = mock_router.route_message.call_args[0][1]
        assert "Plain text body" in incoming.text
        assert incoming.from_name == "jay@example.com"

    def test_send_reply(self):
        from tinyagentos.channel_hub.email_connector import EmailConnector

        connector = EmailConnector(
            agent_name="test", router=MagicMock(),
            imap_host="imap.test.com", imap_port=993,
            smtp_host="smtp.test.com", smtp_port=587,
            username="bot@test.com", password="pass",
        )

        response = OutgoingMessage(
            content="Here is your answer",
            buttons=[{"label": "OK", "action": "ok"}],
        )

        with patch("tinyagentos.channel_hub.email_connector.smtplib.SMTP") as mock_smtp:
            mock_server = MagicMock()
            mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_server)
            mock_smtp.return_value.__exit__ = MagicMock(return_value=False)

            connector._send_reply("jay@example.com", "Question", response)

            mock_server.starttls.assert_called_once()
            mock_server.login.assert_called_once_with("bot@test.com", "pass")
            mock_server.send_message.assert_called_once()
            sent_msg = mock_server.send_message.call_args[0][0]
            assert sent_msg["To"] == "jay@example.com"
            assert sent_msg["Subject"] == "Re: Question"

    def test_send_reply_error_logged(self):
        from tinyagentos.channel_hub.email_connector import EmailConnector

        connector = EmailConnector(
            agent_name="test", router=MagicMock(),
            imap_host="imap.test.com", imap_port=993,
            smtp_host="smtp.test.com", smtp_port=587,
            username="bot@test.com", password="pass",
        )

        response = OutgoingMessage(content="reply")

        with patch("tinyagentos.channel_hub.email_connector.smtplib.SMTP", side_effect=Exception("conn fail")):
            # Should not raise
            connector._send_reply("jay@example.com", "Test", response)

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        from tinyagentos.channel_hub.email_connector import EmailConnector

        connector = EmailConnector(
            agent_name="test", router=MagicMock(),
            imap_host="imap.test.com", imap_port=993,
            smtp_host="smtp.test.com", smtp_port=587,
            username="bot@test.com", password="pass",
        )

        with patch.object(connector, "_poll_loop", new_callable=AsyncMock):
            await connector.start()
            assert connector._running is True

            await connector.stop()
            assert connector._running is False

    def test_check_inbox_imap_error(self):
        from tinyagentos.channel_hub.email_connector import EmailConnector

        connector = EmailConnector(
            agent_name="test", router=MagicMock(),
            imap_host="imap.test.com", imap_port=993,
            smtp_host="smtp.test.com", smtp_port=587,
            username="bot@test.com", password="pass",
        )

        with patch("tinyagentos.channel_hub.email_connector.imaplib.IMAP4_SSL", side_effect=Exception("conn fail")):
            # Should not raise
            connector._check_inbox()

    def test_from_name_parsing(self):
        from tinyagentos.channel_hub.email_connector import EmailConnector

        mock_router = MagicMock()
        mock_router.route_message = AsyncMock(return_value=None)

        connector = EmailConnector(
            agent_name="test", router=mock_router,
            imap_host="imap.test.com", imap_port=993,
            smtp_host="smtp.test.com", smtp_port=587,
            username="bot@test.com", password="pass",
        )

        raw = (
            b'"Jay Kumar" <jay@example.com>\r\n'
            b"Subject: Test\r\n"
            b"Message-ID: <msg3@example.com>\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"body"
        )
        # Manually construct with From header
        raw2 = (
            b"From: \"Jay Kumar\" <jay@example.com>\r\n"
            b"Subject: Test\r\n"
            b"Message-ID: <msg3@example.com>\r\n"
            b"Content-Type: text/plain\r\n"
            b"\r\n"
            b"body"
        )
        msg = email.message_from_bytes(raw2)
        connector._process_email(msg)

        incoming = mock_router.route_message.call_args[0][1]
        assert incoming.from_name == "Jay Kumar"


# ---------------------------------------------------------------------------
# New Framework Adapter health endpoints
# ---------------------------------------------------------------------------

class TestNewAdapterStructure:
    def test_picoclaw_adapter_has_health(self):
        from tinyagentos.adapters.picoclaw_adapter import app
        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/message" in routes

    def test_zeroclaw_adapter_has_health(self):
        from tinyagentos.adapters.zeroclaw_adapter import app
        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/message" in routes

    def test_agent_zero_adapter_has_health(self):
        from tinyagentos.adapters.agent_zero_adapter import app
        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/message" in routes

    def test_hermes_adapter_has_health(self):
        from tinyagentos.adapters.hermes_adapter import app
        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/message" in routes

    def test_nanoclaw_adapter_has_health(self):
        from tinyagentos.adapters.nanoclaw_adapter import app
        routes = [r.path for r in app.routes]
        assert "/health" in routes
        assert "/message" in routes


# ---------------------------------------------------------------------------
# Channel Hub API — Slack and Email connect endpoints
# ---------------------------------------------------------------------------

class TestChannelHubAPISlackEmail:
    @pytest.mark.asyncio
    async def test_connect_slack_secret_not_found(self, client):
        resp = await client.post("/api/channel-hub/connect", json={
            "platform": "slack",
            "bot_token_secret": "nonexistent_slack_secret",
            "agent_name": "test-agent",
        })
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_connect_email_secret_not_found(self, client):
        resp = await client.post("/api/channel-hub/connect", json={
            "platform": "email",
            "bot_token_secret": "nonexistent_email_creds",
            "agent_name": "test-agent",
            "imap_host": "imap.test.com",
            "smtp_host": "smtp.test.com",
        })
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_connect_slack_success(self, client):
        await client.post("/api/secrets", json={"name": "slack_token", "value": "xoxb-fake-token"})

        with patch("tinyagentos.channel_hub.slack_connector.httpx.AsyncClient") as mock_httpx:
            mock_client_instance = AsyncMock()
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"ok": True, "user_id": "U_BOT"}
            mock_client_instance.post = AsyncMock(return_value=mock_resp)
            mock_client_instance.__aenter__ = AsyncMock(return_value=mock_client_instance)
            mock_client_instance.__aexit__ = AsyncMock(return_value=False)
            mock_httpx.return_value = mock_client_instance

            resp = await client.post("/api/channel-hub/connect", json={
                "platform": "slack",
                "bot_token_secret": "slack_token",
                "agent_name": "slack-agent",
                "channel_ids": ["C123"],
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "connected"
            assert data["platform"] == "slack"

    @pytest.mark.asyncio
    async def test_connect_email_success(self, client):
        await client.post("/api/secrets", json={"name": "email_creds", "value": "bot@test.com:password123"})

        with patch("tinyagentos.channel_hub.email_connector.EmailConnector._poll_loop", new_callable=AsyncMock):
            resp = await client.post("/api/channel-hub/connect", json={
                "platform": "email",
                "bot_token_secret": "email_creds",
                "agent_name": "email-agent",
                "imap_host": "imap.test.com",
                "smtp_host": "smtp.test.com",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "connected"
            assert data["platform"] == "email"
