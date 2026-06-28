"""Tests for the Matrix connector. Uses fake nio objects so no homeserver is needed."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tinyagentos.channel_hub.matrix_connector import MatrixConnector
from tinyagentos.channel_hub.message import IncomingMessage, OutgoingMessage


# ---------------------------------------------------------------------------
# Fake nio objects
# ---------------------------------------------------------------------------

class FakeRoom:
    def __init__(self, room_id: str, display_name: str | None = None):
        self.room_id = room_id
        self.display_name = display_name


class FakeRoomMessageText:
    """Minimal stand-in for nio.RoomMessageText."""
    def __init__(self, event_id: str, sender: str, body: str):
        self.event_id = event_id
        self.sender = sender
        self.body = body


class FakeWhoamiResponse:
    def __init__(self, user_id: str):
        self.user_id = user_id


class FakeAsyncClient:
    """Minimal fake for nio.AsyncClient."""
    def __init__(self, homeserver: str):
        self.homeserver = homeserver
        self.access_token: str | None = None
        self._callbacks: list = []
        self.room_send = AsyncMock(return_value=None)
        self.close = AsyncMock(return_value=None)
        self._sync_forever_called = False

    async def whoami(self) -> FakeWhoamiResponse:
        return FakeWhoamiResponse(user_id="@bot:example.org")

    def add_event_callback(self, callback, event_type):
        self._callbacks.append((callback, event_type))

    async def sync_forever(self, timeout=30000, full_state=False):
        # Block until cancelled so the task behaves like the real sync_forever.
        self._sync_forever_called = True
        await asyncio.sleep(9999)

    async def fire_event(self, room, event):
        """Test helper: invoke registered callbacks with a fake event."""
        for cb, _et in self._callbacks:
            await cb(room, event)


def make_connector(agent_name="test-agent"):
    fake_client = FakeAsyncClient("https://example.org")
    mock_router = AsyncMock()
    mock_router.route_message = AsyncMock(return_value=None)

    def factory(homeserver: str):
        return fake_client

    connector = MatrixConnector(
        homeserver="https://example.org",
        access_token="tok_abc",
        agent_name=agent_name,
        router=mock_router,
        client_factory=factory,
    )
    return connector, fake_client, mock_router


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMatrixConnectorStart:
    @pytest.mark.asyncio
    async def test_start_registers_callback_and_spawns_sync(self):
        connector, client, _ = make_connector()
        await connector.start()
        try:
            assert connector._client is client
            assert client.access_token == "tok_abc"
            assert len(client._callbacks) == 1
            assert connector._task is not None
            assert not connector._task.done()
        finally:
            await connector.stop()

    @pytest.mark.asyncio
    async def test_start_sets_own_user_id_from_whoami(self):
        connector, client, _ = make_connector()
        await connector.start()
        try:
            assert connector._own_user_id == "@bot:example.org"
        finally:
            await connector.stop()

    @pytest.mark.asyncio
    async def test_start_raises_when_whoami_has_no_user_id(self):
        """Without a resolvable own user_id the bot would echo-loop, so start
        must refuse rather than register the callback and sync."""
        connector, client, _ = make_connector()

        async def _no_user_id():
            return FakeWhoamiResponse(user_id=None)

        client.whoami = _no_user_id
        with pytest.raises(RuntimeError):
            await connector.start()
        # It must not have left a running sync task or a callback behind.
        assert connector._task is None
        assert client._callbacks == []
        assert client.close.await_count == 1


class TestMatrixInboundRouting:
    @pytest.mark.asyncio
    async def test_inbound_text_event_calls_route_message(self):
        connector, client, router = make_connector()
        router.route_message = AsyncMock(return_value=None)
        await connector.start()
        try:
            room = FakeRoom("!room1:example.org", display_name="Test Room")
            event = FakeRoomMessageText("$evt1", "@alice:example.org", "Hello bot")

            # Patch isinstance check so FakeRoomMessageText passes the nio type guard.
            import tinyagentos.channel_hub.matrix_connector as mod
            with patch.dict("sys.modules", {"nio": MagicMock(RoomMessageText=FakeRoomMessageText)}):
                await client.fire_event(room, event)

            router.route_message.assert_called_once()
            call_args = router.route_message.call_args
            assert call_args[0][0] == "test-agent"
            incoming: IncomingMessage = call_args[0][1]
            assert incoming.platform == "matrix"
            assert incoming.id == "$evt1"
            assert incoming.from_id == "@alice:example.org"
            assert incoming.channel_id == "!room1:example.org"
            assert incoming.channel_name == "Test Room"
            assert incoming.text == "Hello bot"
        finally:
            await connector.stop()

    @pytest.mark.asyncio
    async def test_own_message_is_ignored(self):
        connector, client, router = make_connector()
        router.route_message = AsyncMock(return_value=None)
        await connector.start()
        try:
            room = FakeRoom("!room1:example.org")
            # Sender is the bot's own user_id
            event = FakeRoomMessageText("$own", "@bot:example.org", "I said this")
            with patch.dict("sys.modules", {"nio": MagicMock(RoomMessageText=FakeRoomMessageText)}):
                await client.fire_event(room, event)
            router.route_message.assert_not_called()
        finally:
            await connector.stop()


class TestMatrixOutboundSend:
    @pytest.mark.asyncio
    async def test_outgoing_message_calls_room_send(self):
        connector, client, router = make_connector()
        response = OutgoingMessage(content="Hi there!")
        router.route_message = AsyncMock(return_value=response)
        await connector.start()
        try:
            room = FakeRoom("!room1:example.org")
            event = FakeRoomMessageText("$evt2", "@alice:example.org", "ping")
            with patch.dict("sys.modules", {"nio": MagicMock(RoomMessageText=FakeRoomMessageText)}):
                await client.fire_event(room, event)

            client.room_send.assert_called_once()
            call_kwargs = client.room_send.call_args[1]
            assert call_kwargs["room_id"] == "!room1:example.org"
            assert call_kwargs["message_type"] == "m.room.message"
            assert call_kwargs["content"]["msgtype"] == "m.text"
            assert call_kwargs["content"]["body"] == "Hi there!"
        finally:
            await connector.stop()

    @pytest.mark.asyncio
    async def test_empty_response_does_not_call_room_send(self):
        connector, client, router = make_connector()
        router.route_message = AsyncMock(return_value=OutgoingMessage(content=""))
        await connector.start()
        try:
            room = FakeRoom("!room1:example.org")
            event = FakeRoomMessageText("$evt3", "@alice:example.org", "anything")
            with patch.dict("sys.modules", {"nio": MagicMock(RoomMessageText=FakeRoomMessageText)}):
                await client.fire_event(room, event)
            client.room_send.assert_not_called()
        finally:
            await connector.stop()


class TestMatrixStop:
    @pytest.mark.asyncio
    async def test_stop_cancels_task_and_closes_client(self):
        connector, client, _ = make_connector()
        await connector.start()
        task = connector._task
        assert not task.done()
        await connector.stop()
        assert task.done()
        client.close.assert_called_once()
        assert connector._client is None
        assert connector._task is None

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self):
        connector, client, _ = make_connector()
        await connector.start()
        await connector.stop()
        # Second stop should not raise.
        await connector.stop()
        # close should only have been called once (first stop)
        client.close.assert_called_once()
