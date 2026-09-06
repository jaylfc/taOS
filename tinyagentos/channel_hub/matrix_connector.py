from __future__ import annotations
import asyncio
import logging
from typing import Any, Callable
from tinyagentos.channel_hub.message import IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)

# Default factory so tests can inject a fake client without touching imports.
def _default_client_factory(homeserver: str):
    import nio
    return nio.AsyncClient(homeserver)


class MatrixConnector:
    def __init__(
        self,
        homeserver: str,
        access_token: str,
        agent_name: str,
        router,
        *,
        client_factory: Callable[[str], Any] | None = None,
    ):
        self.homeserver = homeserver
        self.access_token = access_token
        self.agent_name = agent_name
        self.router = router
        self._client_factory = client_factory or _default_client_factory
        self._client = None
        self._task = None
        self._own_user_id: str | None = None

    async def start(self):
        self._client = self._client_factory(self.homeserver)
        self._client.access_token = self.access_token

        # Resolve the bot's own user_id so we can filter echo events.
        try:
            resp = await self._client.whoami()
            self._own_user_id = getattr(resp, "user_id", None)
        except Exception as exc:
            logger.warning("Matrix whoami failed: %s", exc)

        if not self._own_user_id:
            # Without our own user_id we cannot filter our own messages and
            # would echo-loop on every reply, so refuse to start rather than
            # spam the room. A failed whoami usually means an invalid token.
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None
            raise RuntimeError(
                "Matrix connector could not resolve its own user_id "
                "(whoami failed; is the access token valid?)"
            )

        self._client.add_event_callback(self._handle_room_message, None)
        self._task = asyncio.create_task(self._sync_loop())
        logger.info("Matrix connector started for agent '%s'", self.agent_name)

    async def stop(self):
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        if self._client:
            try:
                await self._client.close()
            except Exception as exc:
                logger.debug("Matrix client close error: %s", exc)
            self._client = None

    async def _sync_loop(self):
        try:
            await self._client.sync_forever(timeout=30000, full_state=False)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("Matrix sync error: %s", exc)

    async def _handle_room_message(self, room, event):
        # Only handle text messages and ignore our own events. If matrix-nio is
        # somehow unavailable we cannot classify the event, so do not process it
        # (rather than swallowing the import error and treating it as text).
        try:
            import nio
        except Exception as exc:  # noqa: BLE001
            logger.error("Matrix: matrix-nio unavailable, dropping event: %s", exc)
            return
        if not isinstance(event, nio.RoomMessageText):
            return
        # start() guarantees _own_user_id is set, so this reliably drops echoes.
        if event.sender == self._own_user_id:
            return

        room_id = room.room_id
        room_name = getattr(room, "display_name", None) or room_id

        incoming = IncomingMessage(
            id=event.event_id,
            from_id=event.sender,
            from_name=event.sender,
            platform="matrix",
            channel_id=room_id,
            channel_name=room_name,
            text=event.body,
            raw={"room_id": room_id, "event_id": event.event_id, "sender": event.sender},
        )

        response = await self.router.route_message(self.agent_name, incoming)
        if response and response.content:
            await self._send_response(room_id, response)

    async def _send_response(self, room_id: str, response: OutgoingMessage):
        try:
            await self._client.room_send(
                room_id=room_id,
                message_type="m.room.message",
                content={"msgtype": "m.text", "body": response.content},
            )
        except Exception as exc:
            logger.error("Matrix send error in room %s: %s", room_id, exc)
