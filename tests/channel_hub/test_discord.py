"""Tests for the Discord channel adapter.

Mirrors tinyagentos/channel_hub/adapters/discord.py structure.
Tests cover: init with token, message conversion, sending, rate limits,
missing token, reconnection, and source="discord" verification.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tinyagentos.channel_hub.adapters.discord import (
    DISCORD_API_BASE,
    DiscordConnector,
)
from tinyagentos.channel_hub.message import IncomingMessage, OutgoingMessage
from tinyagentos.channel_hub.router import MessageRouter


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_mock_httpx_response(status_code: int, json_data: object) -> MagicMock:
    """Create a non-awaitable MagicMock that mimics an httpx.Response.

    The httpx client's .get()/.post() return the response via await,
    so the caller must wrap this in an AsyncMock.
    """
    resp = MagicMock()
    resp.status_code = status_code
    resp.json = MagicMock(return_value=json_data)
    return resp


def _async_client_with_get(status_code: int = 200, json_data: object = None):
    """Create an AsyncMock client whose .get() returns a mock response."""
    client = AsyncMock()
    client.get.return_value = _make_mock_httpx_response(
        status_code, json_data if json_data is not None else [],
    )
    # .post() defaults to a successful empty response
    client.post.return_value = _make_mock_httpx_response(200, {})
    return client


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def mock_router():
    """Router mock that echoes a simple text response by default."""
    router = AsyncMock(spec=MessageRouter)
    router.route_message.return_value = OutgoingMessage(content="hello")
    return router


@pytest.fixture
def connector(mock_router):
    """Baseline DiscordConnector with a bot token and one channel."""
    return DiscordConnector(
        bot_token="fake-token-123",
        agent_name="test-agent",
        router=mock_router,
        channel_ids=["123456789"],
    )


@pytest.fixture
def sample_discord_message():
    """A realistic Discord message JSON payload."""
    return {
        "id": "1000000000000000001",
        "channel_id": "123456789",
        "guild_id": "987654321",
        "author": {
            "id": "222222222222222222",
            "username": "test_user",
            "global_name": "Test User",
        },
        "content": "Hello from Discord!",
        "timestamp": "2026-05-31T12:00:00.000000+00:00",
    }


# ------------------------------------------------------------------
# Initialization and token handling
# ------------------------------------------------------------------


class TestInitialization:
    """Tests for DiscordConnector.__init__ and token/auth setup."""

    def test_stores_bot_token(self, connector):
        assert connector.bot_token == "fake-token-123"
        assert connector.headers == {"Authorization": "Bot fake-token-123"}

    def test_default_channel_ids_empty(self, mock_router):
        c = DiscordConnector(
            bot_token="tok", agent_name="a", router=mock_router,
        )
        assert c.channel_ids == []

    def test_stores_agent_name(self, connector):
        assert connector.agent_name == "test-agent"

    def test_initial_running_state(self, connector):
        assert connector._running is False
        assert connector._task is None

    def test_missing_token_is_empty_string(self, mock_router):
        c = DiscordConnector(
            bot_token="", agent_name="a", router=mock_router,
        )
        assert c.bot_token == ""
        assert c.headers == {"Authorization": "Bot "}

    def test_stores_last_message_ids_dict(self, connector):
        assert isinstance(connector._last_message_ids, dict)
        assert len(connector._last_message_ids) == 0

    def test_initial_bot_user_id_none(self, connector):
        """_bot_user_id is None until start() resolves it from /users/@me."""
        assert connector._bot_user_id is None


# ------------------------------------------------------------------
# Start / stop lifecycle
# ------------------------------------------------------------------


class TestStartStop:
    """Tests for start(), stop(), and the poll loop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_sets_running_flag(self, connector):
        with patch(
            "tinyagentos.channel_hub.adapters.discord.httpx.AsyncClient"
        ) as mock_client_cls:
            # The client.__aenter__ returns an AsyncMock
            mock_client = AsyncMock()
            mock_client.get.return_value = _make_mock_httpx_response(
                200, {"id": "99999"},
            )
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            await connector.start()

        assert connector._running is True
        assert connector._task is not None
        assert connector._bot_user_id == "99999"

    @pytest.mark.asyncio
    async def test_start_resolves_bot_user_id_failure(self, connector):
        """A failed /users/@me call (e.g. bad token) raises RuntimeError."""
        with patch(
            "tinyagentos.channel_hub.adapters.discord.httpx.AsyncClient"
        ) as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = _make_mock_httpx_response(
                401, {"message": "Unauthorized"},
            )
            mock_client_cls.return_value.__aenter__.return_value = mock_client

            with pytest.raises(RuntimeError, match="returned an empty user ID"):
                await connector.start()

        assert connector._bot_user_id is None

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self, connector):
        connector._running = True
        fake_task = MagicMock()
        connector._task = fake_task

        await connector.stop()

        assert connector._running is False
        fake_task.cancel.assert_called_once()
        assert connector._task is None

    @pytest.mark.asyncio
    async def test_stop_no_task_is_safe(self, connector):
        connector._running = False
        connector._task = None

        await connector.stop()

        assert connector._running is False


# ------------------------------------------------------------------
# Message conversion (incoming)
# ------------------------------------------------------------------


class TestIncomingMessageConversion:
    """Tests for _handle_message → IncomingMessage creation."""

    @pytest.mark.asyncio
    async def test_source_is_set_to_discord(
        self, connector, sample_discord_message,
    ):
        """The raw payload has source='discord'."""
        client = _async_client_with_get()
        await connector._handle_message(client, "123456789", sample_discord_message)

        incoming = connector.router.route_message.call_args[0][1]
        assert incoming.raw["source"] == "discord"

    @pytest.mark.asyncio
    async def test_converts_message_id(
        self, connector, sample_discord_message,
    ):
        client = _async_client_with_get()
        await connector._handle_message(client, "123456789", sample_discord_message)

        incoming = connector.router.route_message.call_args[0][1]
        assert incoming.id == "1000000000000000001"

    @pytest.mark.asyncio
    async def test_converts_author_id_and_name(
        self, connector, sample_discord_message,
    ):
        client = _async_client_with_get()
        await connector._handle_message(client, "123456789", sample_discord_message)

        incoming = connector.router.route_message.call_args[0][1]
        assert incoming.from_id == "222222222222222222"
        assert incoming.from_name == "test_user"

    @pytest.mark.asyncio
    async def test_converts_fallback_to_global_name(self, connector):
        msg = {
            "id": "1", "channel_id": "c",
            "author": {"id": "999", "global_name": "Global Name"},
            "content": "hi",
        }
        client = _async_client_with_get()
        await connector._handle_message(client, "c", msg)

        incoming = connector.router.route_message.call_args[0][1]
        assert incoming.from_name == "Global Name"

    @pytest.mark.asyncio
    async def test_converts_fallback_to_user_literal_when_no_name(
        self, connector,
    ):
        msg = {
            "id": "1", "channel_id": "c",
            "author": {"id": "999"}, "content": "hi",
        }
        client = _async_client_with_get()
        await connector._handle_message(client, "c", msg)

        incoming = connector.router.route_message.call_args[0][1]
        assert incoming.from_name == "User"

    @pytest.mark.asyncio
    async def test_converts_platform_to_discord(
        self, connector, sample_discord_message,
    ):
        client = _async_client_with_get()
        await connector._handle_message(client, "123456789", sample_discord_message)

        incoming = connector.router.route_message.call_args[0][1]
        assert incoming.platform == "discord"

    @pytest.mark.asyncio
    async def test_converts_channel_id(
        self, connector, sample_discord_message,
    ):
        client = _async_client_with_get()
        await connector._handle_message(client, "123456789", sample_discord_message)

        incoming = connector.router.route_message.call_args[0][1]
        assert incoming.channel_id == "123456789"

    @pytest.mark.asyncio
    async def test_channel_name_with_guild(
        self, connector, sample_discord_message,
    ):
        client = _async_client_with_get()
        await connector._handle_message(client, "ch1", sample_discord_message)

        incoming = connector.router.route_message.call_args[0][1]
        assert incoming.channel_name == "discord:987654321:ch1"

    @pytest.mark.asyncio
    async def test_channel_name_without_guild(self, connector):
        msg = {
            "id": "1", "channel_id": "dm123",
            "author": {"id": "999", "username": "u"}, "content": "hi",
        }
        client = _async_client_with_get()
        await connector._handle_message(client, "dm123", msg)

        incoming = connector.router.route_message.call_args[0][1]
        assert incoming.channel_name == "discord:dm:dm123"

    @pytest.mark.asyncio
    async def test_converts_text_content(
        self, connector, sample_discord_message,
    ):
        client = _async_client_with_get()
        await connector._handle_message(client, "123456789", sample_discord_message)

        incoming = connector.router.route_message.call_args[0][1]
        assert incoming.text == "Hello from Discord!"

    @pytest.mark.asyncio
    async def test_raw_payload_includes_guild_and_author(
        self, connector, sample_discord_message,
    ):
        client = _async_client_with_get()
        await connector._handle_message(client, "123456789", sample_discord_message)

        incoming = connector.router.route_message.call_args[0][1]
        assert incoming.raw["guild_id"] == "987654321"
        assert incoming.raw["author"]["username"] == "test_user"
        assert incoming.raw["author"]["global_name"] == "Test User"

    @pytest.mark.asyncio
    async def test_skips_own_messages(
        self, connector, sample_discord_message,
    ):
        """Own messages are filtered in _check_channel before _handle_message."""
        connector._bot_user_id = "222222222222222222"
        client = AsyncMock()
        client.get.return_value = _make_mock_httpx_response(
            200, [sample_discord_message],
        )

        with patch.object(connector, "_handle_message") as mock_handle:
            await connector._check_channel(client, "123456789")

        # _handle_message should never be called for bot's own messages
        mock_handle.assert_not_called()

        # But the message ID is still tracked (newest-first)
        assert connector._last_message_ids["123456789"] == "1000000000000000001"

    @pytest.mark.asyncio
    async def test_does_not_skip_when_bot_user_id_is_none(
        self, connector, sample_discord_message,
    ):
        connector._bot_user_id = None
        client = _async_client_with_get()

        await connector._handle_message(client, "123456789", sample_discord_message)

        connector.router.route_message.assert_called_once()


# ------------------------------------------------------------------
# Sending outgoing messages
# ------------------------------------------------------------------


class TestOutgoingMessageSending:
    """Tests for _send_response → Discord HTTP POST."""

    @pytest.mark.asyncio
    async def test_sends_text_content(self, connector):
        response = OutgoingMessage(content="hello world")
        client = AsyncMock()
        client.post.return_value = _make_mock_httpx_response(200, {})

        await connector._send_response(client, "ch99", response)

        client.post.assert_called_once()
        call_kwargs = client.post.call_args
        assert call_kwargs[0][0] == f"{DISCORD_API_BASE}/channels/ch99/messages"
        assert call_kwargs[1]["json"] == {"content": "hello world"}

    @pytest.mark.asyncio
    async def test_sends_images_as_embeds(self, connector):
        response = OutgoingMessage(
            content="look!",
            images=["https://example.com/img.png", "https://example.com/img2.jpg"],
        )
        client = AsyncMock()
        client.post.return_value = _make_mock_httpx_response(200, {})

        await connector._send_response(client, "ch99", response)

        payload = client.post.call_args[1]["json"]
        assert payload["content"] == "look!"
        assert payload["embeds"] == [
            {"image": {"url": "https://example.com/img.png"}},
            {"image": {"url": "https://example.com/img2.jpg"}},
        ]

    @pytest.mark.asyncio
    async def test_skips_non_http_images(self, connector):
        response = OutgoingMessage(
            images=["/local/path.png", "https://example.com/x.png"],
        )
        client = AsyncMock()
        client.post.return_value = _make_mock_httpx_response(200, {})

        await connector._send_response(client, "ch99", response)

        payload = client.post.call_args[1]["json"]
        assert payload["embeds"] == [
            {"image": {"url": "https://example.com/x.png"}},
        ]

    @pytest.mark.asyncio
    async def test_sends_buttons_as_components(self, connector):
        response = OutgoingMessage(
            buttons=[
                {"label": "Yes", "action": "yes"},
                {"label": "No", "action": "no"},
            ],
        )
        client = AsyncMock()
        client.post.return_value = _make_mock_httpx_response(200, {})

        await connector._send_response(client, "ch99", response)

        payload = client.post.call_args[1]["json"]
        components = payload["components"]
        assert len(components) == 1
        row = components[0]
        assert row["type"] == 1
        assert len(row["components"]) == 2
        assert row["components"][0]["label"] == "Yes"
        assert row["components"][0]["custom_id"] == "yes"
        assert row["components"][1]["label"] == "No"

    @pytest.mark.asyncio
    async def test_buttons_fallback_action_to_label(self, connector):
        response = OutgoingMessage(buttons=[{"label": "Click Here"}])
        client = AsyncMock()
        client.post.return_value = _make_mock_httpx_response(200, {})

        await connector._send_response(client, "ch99", response)

        payload = client.post.call_args[1]["json"]
        btn = payload["components"][0]["components"][0]
        assert btn["custom_id"] == "Click Here"

    @pytest.mark.asyncio
    async def test_buttons_capped_at_5(self, connector):
        response = OutgoingMessage(
            buttons=[{"label": f"Btn {i}"} for i in range(10)],
        )
        client = AsyncMock()
        client.post.return_value = _make_mock_httpx_response(200, {})

        await connector._send_response(client, "ch99", response)

        payload = client.post.call_args[1]["json"]
        row = payload["components"][0]
        assert len(row["components"]) == 5

    @pytest.mark.asyncio
    async def test_passthrough_sends_raw_payload(self, connector):
        response = OutgoingMessage(
            passthrough=True,
            passthrough_platform="discord",
            passthrough_payload={"content": "raw payload", "flags": 4},
        )
        client = AsyncMock()
        client.post.return_value = _make_mock_httpx_response(200, {})

        await connector._send_response(client, "ch99", response)

        client.post.assert_called_once_with(
            f"{DISCORD_API_BASE}/channels/ch99/messages",
            headers=connector.headers,
            json={"content": "raw payload", "flags": 4},
        )

    @pytest.mark.asyncio
    async def test_passthrough_ignored_for_non_discord(self, connector):
        response = OutgoingMessage(
            passthrough=True,
            passthrough_platform="telegram",
            passthrough_payload={"text": "hello"},
            content="Should use this instead",
        )
        client = AsyncMock()
        client.post.return_value = _make_mock_httpx_response(200, {})

        await connector._send_response(client, "ch99", response)

        payload = client.post.call_args[1]["json"]
        assert payload == {"content": "Should use this instead"}

    @pytest.mark.asyncio
    async def test_empty_payload_skips_post(self, connector):
        response = OutgoingMessage()
        client = AsyncMock()

        await connector._send_response(client, "ch99", response)

        client.post.assert_not_called()

    @pytest.mark.asyncio
    async def test_http_error_does_not_raise(self, connector):
        """An httpx.RequestError during send is caught and logged, not raised."""
        response = OutgoingMessage(content="test")
        client = AsyncMock()
        client.post.side_effect = httpx.RequestError("connection refused")

        # Should not raise
        await connector._send_response(client, "ch99", response)


# ------------------------------------------------------------------
# Rate limit handling (HTTP 429)
# ------------------------------------------------------------------


class TestRateLimitHandling:
    """Tests for 429 rate limit detection and retry-after backoff."""

    @pytest.mark.asyncio
    async def test_429_sleeps_retry_after(self, connector):
        client = AsyncMock()
        client.get.return_value = _make_mock_httpx_response(
            429, {"retry_after": 3.5},
        )

        with patch("asyncio.sleep") as mock_sleep:
            await connector._check_channel(client, "123456789")

        mock_sleep.assert_called_once_with(3.5)

    @pytest.mark.asyncio
    async def test_429_default_retry_after(self, connector):
        client = AsyncMock()
        client.get.return_value = _make_mock_httpx_response(
            429, {"message": "rate limited"},
        )

        with patch("asyncio.sleep") as mock_sleep:
            await connector._check_channel(client, "123456789")

        mock_sleep.assert_called_once_with(5.0)  # _RATE_LIMIT_WINDOW

    @pytest.mark.asyncio
    async def test_401_auth_failure_logged(self, connector):
        client = AsyncMock()
        client.get.return_value = _make_mock_httpx_response(
            401, {"message": "Unauthorized"},
        )

        await connector._check_channel(client, "123456789")

        connector.router.route_message.assert_not_called()


# ------------------------------------------------------------------
# Poll loop and reconnection logic
# ------------------------------------------------------------------


class TestPollLoopReconnection:
    """Tests for _poll_loop resilience and error recovery."""

    @pytest.mark.asyncio
    async def test_poll_loop_iterates_channels(self, connector):
        connector._running = True
        connector.channel_ids = ["ch_a", "ch_b"]
        call_count = 0

        async def fake_check(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                connector._running = False

        with patch.object(connector, "_check_channel", side_effect=fake_check):
            with patch(
                "tinyagentos.channel_hub.adapters.discord.httpx.AsyncClient",
            ) as mock_client_cls:
                mock_client = AsyncMock()
                mock_inst = MagicMock()
                mock_inst.__aenter__ = AsyncMock(return_value=mock_client)
                mock_inst.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_inst
                await connector._poll_loop()

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_poll_loop_recovers_from_exception(self, connector):
        connector._running = True
        connector.channel_ids = ["ch_a"]
        call_count = 0

        async def fake_check(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("transient failure")
            connector._running = False

        with patch.object(connector, "_check_channel", side_effect=fake_check):
            with patch(
                "tinyagentos.channel_hub.adapters.discord.httpx.AsyncClient",
            ) as mock_client_cls:
                mock_client = AsyncMock()
                mock_inst = MagicMock()
                mock_inst.__aenter__ = AsyncMock(return_value=mock_client)
                mock_inst.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_inst
                await connector._poll_loop()

        assert call_count == 2

    @pytest.mark.asyncio
    async def test_poll_loop_exits_on_cancelled_error(self, connector):
        connector._running = True
        connector.channel_ids = ["ch_a"]

        async def fake_check(*args, **kwargs):
            raise asyncio.CancelledError()

        with patch.object(connector, "_check_channel", side_effect=fake_check):
            with patch(
                "tinyagentos.channel_hub.adapters.discord.httpx.AsyncClient",
            ) as mock_client_cls:
                mock_client = AsyncMock()
                mock_inst = MagicMock()
                mock_inst.__aenter__ = AsyncMock(return_value=mock_client)
                mock_inst.__aexit__ = AsyncMock(return_value=None)
                mock_client_cls.return_value = mock_inst
                await connector._poll_loop()

        # Loop exits cleanly; _running stays True (not changed by loop)
        assert connector._running is True

    @pytest.mark.asyncio
    async def test_poll_loop_stops_when_not_running(self, connector):
        connector._running = False

        with patch.object(connector, "_check_channel") as mock_check:
            await connector._poll_loop()

        mock_check.assert_not_called()

    @pytest.mark.asyncio
    async def test_http_request_error_is_handled(self, connector):
        client = AsyncMock()
        client.get.side_effect = httpx.RequestError("connection lost")

        await connector._check_channel(client, "123456789")

        # Should not raise

    @pytest.mark.asyncio
    async def test_non_200_status_skips_message_processing(self, connector):
        client = AsyncMock()
        client.get.return_value = _make_mock_httpx_response(
            500, {"message": "server error"},
        )

        await connector._check_channel(client, "123456789")

        connector.router.route_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_200_empty_array_does_nothing(self, connector):
        client = AsyncMock()
        client.get.return_value = _make_mock_httpx_response(200, [])

        await connector._check_channel(client, "123456789")

        connector.router.route_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_200_with_messages_routes_them(self, connector):
        client = AsyncMock()
        msg = {
            "id": "m1",
            "author": {"id": "u1", "username": "Alice"},
            "content": "hi there",
            "channel_id": "123456789",
        }
        client.get.return_value = _make_mock_httpx_response(200, [msg])
        client.post.return_value = _make_mock_httpx_response(200, {})

        await connector._check_channel(client, "123456789")

        connector.router.route_message.assert_called_once()
        incoming = connector.router.route_message.call_args[0][1]
        assert incoming.text == "hi there"
        assert incoming.from_name == "Alice"

    @pytest.mark.asyncio
    async def test_messages_processed_oldest_first(self, connector):
        client = AsyncMock()
        msgs = [
            {"id": "m2", "author": {"id": "u2", "username": "Second"}, "content": "2"},
            {"id": "m1", "author": {"id": "u1", "username": "First"}, "content": "1"},
        ]
        client.get.return_value = _make_mock_httpx_response(200, msgs)
        client.post.return_value = _make_mock_httpx_response(200, {})

        await connector._check_channel(client, "123456789")

        assert connector.router.route_message.call_count == 2
        assert connector.router.route_message.call_args_list[0][0][1].id == "m1"
        assert connector.router.route_message.call_args_list[1][0][1].id == "m2"

    @pytest.mark.asyncio
    async def test_last_message_id_tracked(self, connector):
        client = AsyncMock()
        msgs = [
            {"id": "m2", "author": {"id": "u2", "username": "B"}, "content": "b"},
            {"id": "m1", "author": {"id": "u1", "username": "A"}, "content": "a"},
        ]
        client.get.return_value = _make_mock_httpx_response(200, msgs)
        client.post.return_value = _make_mock_httpx_response(200, {})

        await connector._check_channel(client, "ch_abc")

        assert connector._last_message_ids["ch_abc"] == "m2"


# ------------------------------------------------------------------
# Channel name builder
# ------------------------------------------------------------------


class TestChannelNameBuilder:
    """Tests for the static _build_channel_name helper."""

    def test_with_guild(self):
        name = DiscordConnector._build_channel_name("guild1", "chan1")
        assert name == "discord:guild1:chan1"

    def test_without_guild(self):
        name = DiscordConnector._build_channel_name("", "dm1")
        assert name == "discord:dm:dm1"


# ------------------------------------------------------------------
# Router integration (response → send)
# ------------------------------------------------------------------


class TestRouterResponseToSend:
    """Tests that route_message responses are forwarded to _send_response."""

    @pytest.mark.asyncio
    async def test_router_response_sent_to_channel(
        self, connector, sample_discord_message,
    ):
        connector.router.route_message.return_value = OutgoingMessage(
            content="response",
        )

        client = AsyncMock()
        client.post.return_value = _make_mock_httpx_response(200, {})

        await connector._handle_message(client, "chX", sample_discord_message)

        client.post.assert_called_once()
        call_args = client.post.call_args
        assert call_args[0][0] == f"{DISCORD_API_BASE}/channels/chX/messages"

    @pytest.mark.asyncio
    async def test_none_response_does_not_send(
        self, connector, sample_discord_message,
    ):
        connector.router.route_message.return_value = None
        client = AsyncMock()

        await connector._handle_message(client, "chX", sample_discord_message)

        client.post.assert_not_called()
