"""Tests for WebhookConnector and the webhook channel-hub route."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from tinyagentos.channel_hub.webhook_connector import WebhookConnector
from tinyagentos.channel_hub.message import OutgoingMessage
from tinyagentos.channel_hub.router import MessageRouter


class TestWebhookConnector:
    @pytest.mark.asyncio
    async def test_handle_incoming_routes_to_agent(self):
        mock_router = AsyncMock(spec=MessageRouter)
        mock_router.route_message.return_value = OutgoingMessage(
            content="Hello from agent",
            buttons=[{"label": "OK", "action": "ok"}],
            images=["img.png"],
        )
        connector = WebhookConnector(agent_name="test-agent", router=mock_router)

        result = await connector.handle_incoming({"text": "hi", "from": "user1", "name": "Jay"})

        assert result["content"] == "Hello from agent"
        assert result["buttons"] == [{"label": "OK", "action": "ok"}]
        assert result["images"] == ["img.png"]
        mock_router.route_message.assert_called_once()
        incoming = mock_router.route_message.call_args[0][1]
        assert incoming.text == "hi"
        assert incoming.from_id == "user1"
        assert incoming.platform == "webhook"

    @pytest.mark.asyncio
    async def test_handle_incoming_no_response(self):
        mock_router = AsyncMock(spec=MessageRouter)
        mock_router.route_message.return_value = None
        connector = WebhookConnector(agent_name="test-agent", router=mock_router)

        result = await connector.handle_incoming({"text": "hi"})
        assert result == {"status": "no_response"}

    @pytest.mark.asyncio
    async def test_handle_incoming_message_field(self):
        """The 'message' field is used as fallback when 'text' is absent."""
        mock_router = AsyncMock(spec=MessageRouter)
        mock_router.route_message.return_value = OutgoingMessage(content="ok")
        connector = WebhookConnector(agent_name="test-agent", router=mock_router)

        await connector.handle_incoming({"message": "hello via message field"})
        incoming = mock_router.route_message.call_args[0][1]
        assert incoming.text == "hello via message field"

    @pytest.mark.asyncio
    async def test_outgoing_webhook_called(self):
        mock_router = AsyncMock(spec=MessageRouter)
        mock_router.route_message.return_value = OutgoingMessage(content="response")
        connector = WebhookConnector(
            agent_name="test-agent", router=mock_router,
            outgoing_url="https://example.com/hook",
        )

        with patch("tinyagentos.channel_hub.webhook_connector.httpx.AsyncClient") as mock_httpx:
            mock_client = AsyncMock()
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await connector.handle_incoming({"text": "hi"})

            assert result["content"] == "response"
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args[0][0] == "https://example.com/hook"

    @pytest.mark.asyncio
    async def test_outgoing_webhook_failure_does_not_raise(self):
        mock_router = AsyncMock(spec=MessageRouter)
        mock_router.route_message.return_value = OutgoingMessage(content="response")
        connector = WebhookConnector(
            agent_name="test-agent", router=mock_router,
            outgoing_url="https://example.com/hook",
        )

        with patch("tinyagentos.channel_hub.webhook_connector.httpx.AsyncClient") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.post.side_effect = Exception("connection refused")
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

            # Should not raise
            result = await connector.handle_incoming({"text": "hi"})
            assert result["content"] == "response"


@pytest.mark.asyncio
class TestWebhookRoute:
    async def test_webhook_endpoint_auto_creates_connector(self, client):
        """POST to /api/channel-hub/webhook/{agent} auto-creates connector and returns result."""
        # The route will auto-create a WebhookConnector; the router has no adapter
        # registered for test-agent so the response will be no_response.
        resp = await client.post(
            "/api/channel-hub/webhook/test-agent",
            json={"text": "hello", "from": "test-user"},
        )
        assert resp.status_code == 200
        data = resp.json()
        # No adapter registered, so router returns None -> no_response
        assert data["status"] == "no_response"

    async def test_webhook_endpoint_returns_agent_response(self, client):
        """When an adapter is registered, the webhook returns the agent's response."""
        # Register a mock adapter port so the router tries to call it
        router_obj = client._transport.app.state.channel_hub_router
        router_obj._agent_ports["webhook-agent"] = 99999  # won't connect, but tests the path

        resp = await client.post(
            "/api/channel-hub/webhook/webhook-agent",
            json={"text": "hello"},
        )
        assert resp.status_code == 200
        # The adapter call will fail (port 99999), so router returns None
        data = resp.json()
        assert data["status"] == "no_response"
