"""Tests for the Channel Hub — message format, router, adapters, and API endpoints."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.channel_hub.message import (
    IncomingMessage,
    OutgoingMessage,
    parse_inline_hints,
)
from tinyagentos.channel_hub.router import MessageRouter
from tinyagentos.channel_hub.adapter_manager import AdapterManager


# ---------------------------------------------------------------------------
# IncomingMessage / OutgoingMessage creation
# ---------------------------------------------------------------------------

class TestMessageDataclasses:
    def test_incoming_message_creation(self):
        msg = IncomingMessage(
            id="123",
            from_id="user1",
            from_name="Alice",
            platform="telegram",
            channel_id="chat456",
            channel_name="General",
            text="Hello world",
        )
        assert msg.id == "123"
        assert msg.platform == "telegram"
        assert msg.text == "Hello world"
        assert msg.attachments == []
        assert msg.reply_to is None
        assert isinstance(msg.timestamp, float)
        assert msg.raw == {}

    def test_incoming_message_with_attachments(self):
        msg = IncomingMessage(
            id="1",
            from_id="u1",
            from_name="Bob",
            platform="discord",
            channel_id="ch1",
            channel_name="dev",
            text="check this",
            attachments=[{"type": "image", "url": "http://example.com/img.png"}],
            reply_to="msg99",
        )
        assert len(msg.attachments) == 1
        assert msg.reply_to == "msg99"

    def test_outgoing_message_defaults(self):
        msg = OutgoingMessage()
        assert msg.content == ""
        assert msg.buttons == []
        assert msg.images == []
        assert msg.cards == []
        assert msg.reply_to is None
        assert msg.passthrough is False
        assert msg.passthrough_platform == ""
        assert msg.passthrough_payload == {}

    def test_outgoing_message_with_content(self):
        msg = OutgoingMessage(
            content="Hi there",
            buttons=[{"label": "OK", "action": "ok"}],
            images=["http://example.com/img.png"],
        )
        assert msg.content == "Hi there"
        assert len(msg.buttons) == 1
        assert len(msg.images) == 1


# ---------------------------------------------------------------------------
# parse_inline_hints
# ---------------------------------------------------------------------------

class TestParseInlineHints:
    def test_no_hints(self):
        result = parse_inline_hints("Just plain text")
        assert result.content == "Just plain text"
        assert result.buttons == []
        assert result.images == []

    def test_single_button(self):
        result = parse_inline_hints("Click here [button:Yes:confirm_yes]")
        assert result.content == "Click here"
        assert len(result.buttons) == 1
        assert result.buttons[0] == {"label": "Yes", "action": "confirm_yes"}

    def test_multiple_buttons(self):
        result = parse_inline_hints("[button:A:act_a] middle [button:B:act_b]")
        assert result.content == "middle"
        assert len(result.buttons) == 2
        assert result.buttons[0]["label"] == "A"
        assert result.buttons[1]["label"] == "B"

    def test_single_image(self):
        result = parse_inline_hints("Here is an image [image:/tmp/photo.jpg]")
        assert result.content == "Here is an image"
        assert result.images == ["/tmp/photo.jpg"]

    def test_mixed_buttons_and_images(self):
        result = parse_inline_hints(
            "Result: [button:View:view_it] [image:http://example.com/pic.png]"
        )
        assert "Result:" in result.content
        assert len(result.buttons) == 1
        assert len(result.images) == 1

    def test_empty_text(self):
        result = parse_inline_hints("")
        assert result.content == ""
        assert result.buttons == []
        assert result.images == []

    def test_url_image(self):
        result = parse_inline_hints("[image:https://cdn.example.com/cat.jpg]")
        assert result.images == ["https://cdn.example.com/cat.jpg"]
        assert result.content == ""


# ---------------------------------------------------------------------------
# MessageRouter
# ---------------------------------------------------------------------------

class TestMessageRouter:
    def test_assign_and_get_channel(self):
        router = MessageRouter()
        router.assign_channel("telegram", "bot123", "agent-alpha")
        assert router.get_agent_for_channel("telegram", "bot123") == "agent-alpha"
        assert router.get_agent_for_channel("telegram", "unknown") is None

    def test_register_and_get_adapter(self):
        router = MessageRouter()
        router.register_adapter("my-agent", 9005)
        assert router.get_adapter_port("my-agent") == 9005
        assert router.get_adapter_port("other") is None

    def test_allocate_port(self):
        router = MessageRouter()
        p1 = router.allocate_port("agent-a")
        p2 = router.allocate_port("agent-b")
        assert p1 == 9001
        assert p2 == 9002
        assert router.get_adapter_port("agent-a") == 9001
        assert router.get_adapter_port("agent-b") == 9002

    def test_allocate_port_sequential(self):
        router = MessageRouter()
        ports = [router.allocate_port(f"agent-{i}") for i in range(5)]
        assert ports == [9001, 9002, 9003, 9004, 9005]

    @pytest.mark.asyncio
    async def test_route_message_no_adapter(self):
        router = MessageRouter()
        msg = IncomingMessage(
            id="1", from_id="u", from_name="U", platform="web",
            channel_id="c", channel_name="C", text="hi",
        )
        result = await router.route_message("nonexistent", msg)
        assert result is None


# ---------------------------------------------------------------------------
# AdapterManager port allocation
# ---------------------------------------------------------------------------

class TestAdapterManager:
    def test_port_allocation_via_manager(self):
        router = MessageRouter()
        mgr = AdapterManager(router)
        # AdapterManager delegates to router for port allocation
        port = mgr.router.allocate_port("test-agent")
        assert port == 9001

    def test_stop_adapter_noop_for_unknown(self):
        router = MessageRouter()
        mgr = AdapterManager(router)
        # Should not raise
        mgr.stop_adapter("nonexistent")

    def test_stop_all_empty(self):
        router = MessageRouter()
        mgr = AdapterManager(router)
        mgr.stop_all()  # Should not raise


# ---------------------------------------------------------------------------
# TelegramConnector response building (mock httpx)
# ---------------------------------------------------------------------------

class TestTelegramConnector:
    @pytest.mark.asyncio
    async def test_send_text_response(self):
        from tinyagentos.channel_hub.telegram_connector import TelegramConnector

        router = MagicMock()
        connector = TelegramConnector(bot_token="fake_token", agent_name="test", router=router)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        response = OutgoingMessage(content="Hello back!")
        await connector._send_response(mock_client, 12345, response)

        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert "/sendMessage" in call_args[0][0]
        assert call_args[1]["json"]["text"] == "Hello back!"
        assert call_args[1]["json"]["chat_id"] == 12345

    @pytest.mark.asyncio
    async def test_send_response_with_buttons(self):
        from tinyagentos.channel_hub.telegram_connector import TelegramConnector

        router = MagicMock()
        connector = TelegramConnector(bot_token="fake_token", agent_name="test", router=router)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        response = OutgoingMessage(
            content="Choose:",
            buttons=[{"label": "Yes", "action": "yes"}, {"label": "No", "action": "no"}],
        )
        await connector._send_response(mock_client, 99, response)

        call_args = mock_client.post.call_args
        payload = call_args[1]["json"]
        assert "reply_markup" in payload
        keyboard = payload["reply_markup"]["inline_keyboard"]
        assert len(keyboard) == 2

    @pytest.mark.asyncio
    async def test_send_passthrough(self):
        from tinyagentos.channel_hub.telegram_connector import TelegramConnector

        router = MagicMock()
        connector = TelegramConnector(bot_token="fake_token", agent_name="test", router=router)

        mock_client = AsyncMock()
        mock_client.post = AsyncMock()

        response = OutgoingMessage(
            passthrough=True,
            passthrough_platform="telegram",
            passthrough_payload={"method": "sendSticker", "sticker": "abc"},
        )
        await connector._send_response(mock_client, 42, response)

        call_args = mock_client.post.call_args
        assert "/sendSticker" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_handle_update_routes_message(self):
        from tinyagentos.channel_hub.telegram_connector import TelegramConnector

        mock_router = AsyncMock()
        mock_router.route_message = AsyncMock(return_value=None)
        connector = TelegramConnector(bot_token="fake", agent_name="bot1", router=mock_router)

        mock_client = AsyncMock()
        update = {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "from": {"id": 555, "first_name": "Jay"},
                "chat": {"id": 100, "title": "TestChat"},
                "text": "hello",
            },
        }
        await connector._handle_update(mock_client, update)
        mock_router.route_message.assert_called_once()
        incoming = mock_router.route_message.call_args[0][1]
        assert incoming.text == "hello"
        assert incoming.from_name == "Jay"
        assert incoming.platform == "telegram"

    @pytest.mark.asyncio
    async def test_handle_callback_query(self):
        from tinyagentos.channel_hub.telegram_connector import TelegramConnector

        mock_router = AsyncMock()
        mock_router.route_message = AsyncMock(return_value=None)
        connector = TelegramConnector(bot_token="fake", agent_name="bot1", router=mock_router)

        mock_client = AsyncMock()
        update = {
            "update_id": 2,
            "callback_query": {
                "data": "confirm_yes",
                "message": {
                    "message_id": 11,
                    "from": {"id": 555, "first_name": "Jay"},
                    "chat": {"id": 100, "title": "TestChat"},
                    "text": "old message",
                },
            },
        }
        await connector._handle_update(mock_client, update)
        incoming = mock_router.route_message.call_args[0][1]
        assert incoming.text == "[callback:confirm_yes]"

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        from tinyagentos.channel_hub.telegram_connector import TelegramConnector

        router = MagicMock()
        connector = TelegramConnector(bot_token="fake", agent_name="test", router=router)

        # Patch the poll loop so it does not actually run
        with patch.object(connector, "_poll_loop", new_callable=AsyncMock):
            await connector.start()
            assert connector._running is True
            await connector.stop()
            assert connector._running is False


# ---------------------------------------------------------------------------
# Channel Hub API endpoints
# ---------------------------------------------------------------------------

class TestChannelHubAPI:
    @pytest.mark.asyncio
    async def test_status_endpoint(self, client):
        resp = await client.get("/api/channel-hub/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "connectors" in data
        assert "adapters" in data
        assert "channel_assignments" in data

    @pytest.mark.asyncio
    async def test_adapters_endpoint(self, client):
        resp = await client.get("/api/channel-hub/adapters")
        assert resp.status_code == 200
        data = resp.json()
        assert "adapters" in data
        assert isinstance(data["adapters"], list)

    @pytest.mark.asyncio
    async def test_connect_missing_fields(self, client):
        resp = await client.post("/api/channel-hub/connect", json={"platform": "telegram"})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_connect_secret_not_found(self, client):
        resp = await client.post("/api/channel-hub/connect", json={
            "platform": "telegram",
            "bot_token_secret": "nonexistent_secret",
            "agent_name": "test-agent",
        })
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_connect_unsupported_platform(self, client):
        # First store a secret so we get past the secret check
        await client.post("/api/secrets", json={"name": "test_token", "value": "fake"})
        resp = await client.post("/api/channel-hub/connect", json={
            "platform": "irc",
            "bot_token_secret": "test_token",
            "agent_name": "test-agent",
        })
        assert resp.status_code == 400
        assert "not yet supported" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_disconnect_not_found(self, client):
        resp = await client.post("/api/channel-hub/disconnect", json={
            "platform": "telegram",
            "agent_name": "nobody",
        })
        assert resp.status_code == 404
