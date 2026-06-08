from __future__ import annotations

import asyncio
import json
import time
from fastapi import WebSocket

_TYPING_TTL = 5.0  # seconds


class ChatHub:
    def __init__(self):
        self._channels: dict[str, set[WebSocket]] = {}
        self._user_sockets: dict[str, set[WebSocket]] = {}
        self._presence: dict[str, dict] = {}
        self._typing: dict[str, dict[str, float]] = {}
        self._seq = 0

    def connect(self, ws: WebSocket, user_id: str) -> None:
        self._user_sockets.setdefault(user_id, set()).add(ws)
        self._presence[user_id] = {"status": "online", "last_seen": time.time()}

    def disconnect(self, ws: WebSocket, user_id: str) -> None:
        sockets = self._user_sockets.get(user_id, set())
        sockets.discard(ws)
        if not sockets:
            self._presence[user_id] = {"status": "offline", "last_seen": time.time()}
        # Remove from all channels
        for channel_sockets in self._channels.values():
            channel_sockets.discard(ws)

    def join(self, ws: WebSocket, channel_id: str) -> None:
        self._channels.setdefault(channel_id, set()).add(ws)

    def leave(self, ws: WebSocket, channel_id: str) -> None:
        channel_sockets = self._channels.get(channel_id)
        if channel_sockets:
            channel_sockets.discard(ws)

    async def broadcast(self, channel_id: str, event: dict) -> None:
        payload = json.dumps(event)
        for ws in list(self._channels.get(channel_id, set())):
            try:
                await ws.send_text(payload)
            except Exception:
                pass  # skip failures silently

    async def send_to_user(self, user_id: str, event: dict) -> None:
        payload = json.dumps(event)
        for ws in list(self._user_sockets.get(user_id, set())):
            try:
                await ws.send_text(payload)
            except Exception:
                pass

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
