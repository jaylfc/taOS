"""Durable outbox for guest-bound bridge events (tsk-ph6mwg).

A guest (registry agent with a chat_session grant) has no container, so
its pending user_message events must survive a controller restart. Rows are
written at enqueue time and stamped ``delivered_at`` once streamed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

GUEST_OUTBOX_SCHEMA = """
CREATE TABLE IF NOT EXISTS guest_outbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at REAL NOT NULL,
    delivered_at REAL
);
CREATE INDEX IF NOT EXISTS idx_guest_outbox_slug ON guest_outbox(slug, id);
"""

PRUNE_AFTER_SECONDS = 7 * 86400


class GuestOutbox(BaseStore):
    SCHEMA = GUEST_OUTBOX_SCHEMA

    async def append(self, slug: str, payload: dict) -> int:
        cur = await self._db.execute(
            "INSERT INTO guest_outbox (slug, payload, created_at) VALUES (?, ?, ?)",
            (slug, json.dumps(payload), time.time()),
        )
        await self._db.commit()
        return cur.lastrowid

    async def _rows(self, sql: str, params: tuple) -> list[tuple[int, dict]]:
        async with self._db.execute(sql, params) as cur:
            return [(r[0], json.loads(r[1])) for r in await cur.fetchall()]

    async def since(self, slug: str, last_id: int) -> list[tuple[int, dict]]:
        """Rows for *slug* with id strictly greater than *last_id*, oldest first."""
        return await self._rows(
            "SELECT id, payload FROM guest_outbox WHERE slug = ? AND id > ? ORDER BY id",
            (slug, last_id),
        )

    async def undelivered(self, slug: str) -> list[tuple[int, dict]]:
        return await self._rows(
            "SELECT id, payload FROM guest_outbox "
            "WHERE slug = ? AND delivered_at IS NULL ORDER BY id",
            (slug,),
        )

    async def mark_delivered(self, row_id: int) -> None:
        await self._db.execute(
            "UPDATE guest_outbox SET delivered_at = ? WHERE id = ?",
            (time.time(), row_id),
        )
        await self._db.commit()

    async def prune(self, older_than_seconds: float = PRUNE_AFTER_SECONDS) -> None:
        await self._db.execute(
            "DELETE FROM guest_outbox WHERE created_at < ?",
            (time.time() - older_than_seconds,),
        )
        await self._db.commit()


async def prune_loop(outbox: GuestOutbox, interval: float = 6 * 3600) -> None:
    while True:
        try:
            await outbox.prune()
        except Exception:
            logger.exception("guest outbox prune failed")
        await asyncio.sleep(interval)
