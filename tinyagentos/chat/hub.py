from __future__ import annotations

import asyncio
import json
import time
from fastapi import WebSocket

_TYPING_TTL = 5.0  # seconds
_DEFAULT_SEND_TIMEOUT = 5.0  # seconds before a slow client is closed
_DEFAULT_MAX_CONNECTIONS_PER_IP = 10


class ChatHub:
    def __init__(
        self,
        send_timeout: float = _DEFAULT_SEND_TIMEOUT,
        max_connections_per_ip: int = _DEFAULT_MAX_CONNECTIONS_PER_IP,
    ):
        self._channels: dict[str, set[WebSocket]] = {}
        self._user_sockets: dict[str, set[WebSocket]] = {}
        self._presence: dict[str, dict] = {}
        self._typing: dict[str, dict[str, float]] = {}
        self._seq = 0
        self._send_timeout = send_timeout
        self._max_connections_per_ip = max_connections_per_ip
        # Tracks active socket count per IP address
        self._ip_counts: dict[str, int] = {}

    def _ip_of(self, ws) -> str | None:
        """Extract the client IP from a WebSocket, if available."""
        client = getattr(ws, "client", None)
        if client is None:
            return None
        return getattr(client, "host", None)

    def connect(self, ws: WebSocket, user_id: str) -> bool:
        """Register a connection.  Returns False if the per-IP cap is exceeded."""
        ip = self._ip_of(ws)
        if ip is not None:
            count = self._ip_counts.get(ip, 0)
            if count >= self._max_connections_per_ip:
                return False
            self._ip_counts[ip] = count + 1
        self._user_sockets.setdefault(user_id, set()).add(ws)
        self._presence[user_id] = {"status": "online", "last_seen": time.time()}
        return True

    def disconnect(self, ws: WebSocket, user_id: str) -> None:
        sockets = self._user_sockets.get(user_id, set())
        sockets.discard(ws)
        if not sockets:
            self._presence[user_id] = {"status": "offline", "last_seen": time.time()}
        # Remove from all channels
        for channel_sockets in self._channels.values():
            channel_sockets.discard(ws)
        # Release IP slot
        ip = self._ip_of(ws)
        if ip is not None and ip in self._ip_counts:
            self._ip_counts[ip] = max(0, self._ip_counts[ip] - 1)
            if self._ip_counts[ip] == 0:
                del self._ip_counts[ip]

    def join(self, ws: WebSocket, channel_id: str) -> None:
        self._channels.setdefault(channel_id, set()).add(ws)

    def leave(self, ws: WebSocket, channel_id: str) -> None:
        channel_sockets = self._channels.get(channel_id)
        if channel_sockets:
            channel_sockets.discard(ws)

    async def _send_with_timeout(self, ws, payload: str, collection: set | None = None) -> None:
        """Send ``payload`` to ``ws``, closing and removing it on timeout or error."""
        try:
            await asyncio.wait_for(ws.send_text(payload), timeout=self._send_timeout)
        except Exception:
            # Timeout or any send error — close the dead socket and evict it
            try:
                await ws.close()
            except Exception:
                pass
            if collection is not None:
                collection.discard(ws)
            # Also evict from _user_sockets
            for sockets in self._user_sockets.values():
                sockets.discard(ws)

    async def broadcast(self, channel_id: str, event: dict) -> None:
        payload = json.dumps(event)
        channel_sockets = self._channels.get(channel_id)
        if not channel_sockets:
            return
        for ws in list(channel_sockets):
            await self._send_with_timeout(ws, payload, collection=channel_sockets)

    async def send_to_user(self, user_id: str, event: dict) -> None:
        payload = json.dumps(event)
        sockets = self._user_sockets.get(user_id)
        if not sockets:
            return
        for ws in list(sockets):
            await self._send_with_timeout(ws, payload, collection=sockets)

    def set_typing(self, channel_id: str, user_id: str) -> None:
        self._typing.setdefault(channel_id, {})[user_id] = time.time()

    def get_typing(self, channel_id: str) -> list[str]:
        now = time.time()
        channel_typing = self._typing.get(channel_id, {})
        return [uid for uid, ts in channel_typing.items() if now - ts < _TYPING_TTL]

    def next_seq(self) -> int:
        self._seq += 1
        return self._seq

    async def seed_seq(self, store) -> None:
        """Seed the sequence counter from the message store on startup.

        Queries MAX(rowid) from chat_messages so the counter never resets to 1
        after a restart, preventing client-side sequence number jumps.
        """
        async with store._db.execute(
            "SELECT COALESCE(MAX(rowid), 0) FROM chat_messages"
        ) as cursor:
            row = await cursor.fetchone()
        self._seq = row[0] if row else 0
