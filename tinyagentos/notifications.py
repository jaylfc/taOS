from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

NOTIF_SCHEMA = """
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp INTEGER NOT NULL,
    level TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0,
    source TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    data TEXT
);
CREATE INDEX IF NOT EXISTS idx_notif_ts ON notifications(timestamp DESC);
CREATE TABLE IF NOT EXISTS notification_prefs (
    event_type TEXT PRIMARY KEY,
    muted INTEGER NOT NULL DEFAULT 0
);
"""


def _serialize_row(r) -> dict:
    """Map a notifications row to the API dict, parsing the JSON `data` payload.

    Uses named column access via dict(zip(COLS, r)) so a schema column reorder
    cannot silently corrupt the mapping. The SELECT queries must select exactly
    these columns in this order.
    """
    COLS = ("id", "timestamp", "level", "title", "message", "read", "source", "data")
    row = dict(zip(COLS, r))
    data = None
    if row["data"]:
        try:
            data = json.loads(row["data"])
        except (ValueError, TypeError):
            data = None
    return {
        "id": row["id"], "timestamp": row["timestamp"], "level": row["level"],
        "title": row["title"], "message": row["message"], "read": bool(row["read"]),
        "source": row["source"], "data": data,
    }


class NotificationStore(BaseStore):
    SCHEMA = NOTIF_SCHEMA

    EVENT_TYPES = [
        "worker.join", "worker.online", "worker.leave", "backend.up", "backend.down",
        "training.complete", "training.failed", "app.installed", "app.failed",
        "disk_quota", "task.claimed", "task.closed",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._webhook_notifier = None
        self._event_emitter = None  # async callable: (row: dict) -> None

    def set_webhook_notifier(self, notifier) -> None:
        """Attach a WebhookNotifier to fire on every notification."""
        self._webhook_notifier = notifier

    def set_event_emitter(self, emitter) -> None:
        """Attach an async callable called with each new notification row dict.

        The emitter receives the same shape as the GET /api/notifications items
        (id, timestamp, level, title, message, read, source, data). Used to
        push new notifications to SSE subscribers without polling. Best-effort:
        a failing emitter never breaks add().
        """
        self._event_emitter = emitter

    async def _post_init(self) -> None:
        # `archived` was added after the initial notifications ship. Guarded
        # ALTER so existing databases gain it without a destructive migration
        # (SQLite lacks ADD COLUMN IF NOT EXISTS before 3.37). Dismissing a
        # notification archives it (#62); nothing is hard-deleted by the user.
        cols = {row[1] for row in await (await self._db.execute("PRAGMA table_info(notifications)")).fetchall()}
        if "archived" not in cols:
            await self._db.execute(
                "ALTER TABLE notifications ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
            )
            await self._db.commit()
        # `data` carries a JSON payload for structured notifications (e.g. an
        # agent auth-request's request_id + scopes). Guarded ALTER so existing
        # databases gain the column without a destructive migration.
        if "data" not in cols:
            await self._db.execute(
                "ALTER TABLE notifications ADD COLUMN data TEXT"
            )
            await self._db.commit()

    async def add(
        self,
        title: str,
        message: str,
        level: str = "info",
        source: str = "system",
        data: dict | None = None,
    ) -> None:
        ts = int(time.time())
        data_json = json.dumps(data) if data is not None else None
        cursor = await self._db.execute(
            "INSERT INTO notifications (timestamp, level, title, message, source, data) VALUES (?, ?, ?, ?, ?, ?)",
            (ts, level, title, message, source, data_json),
        )
        await self._db.commit()
        # Fire webhook notifications in the background
        if self._webhook_notifier:
            try:
                await self._webhook_notifier.notify(title, message, level)
            except Exception as e:
                logger.warning(f"Webhook notification error: {e}")
        # Push to EventBus broadcast for SSE delivery — best-effort, never breaks add()
        if self._event_emitter:
            try:
                row = {
                    "id": cursor.lastrowid,
                    "timestamp": ts,
                    "level": level,
                    "title": title,
                    "message": message,
                    "read": False,
                    "source": source,
                    "data": data,
                }
                await self._event_emitter(row)
            except Exception:
                logger.warning("NotificationStore: event emitter failed", exc_info=True)

    async def list(self, limit: int = 20, unread_only: bool = False) -> list[dict]:
        # Active feed: archived (dismissed) notifications are excluded.
        conds = ["archived = 0"]
        if unread_only:
            conds.append("read = 0")
        sql = (
            "SELECT id, timestamp, level, title, message, read, source, data FROM notifications"
            f" WHERE {' AND '.join(conds)} ORDER BY timestamp DESC LIMIT ?"
        )
        async with self._db.execute(sql, (limit,)) as cursor:
            rows = await cursor.fetchall()
        return [_serialize_row(r) for r in rows]

    async def list_archived(self, limit: int = 50) -> list[dict]:
        # History view: the dismissed notifications, newest first. Nothing is
        # deleted, so this is the durable record (#62 / append-only #103).
        async with self._db.execute(
            "SELECT id, timestamp, level, title, message, read, source, data FROM notifications"
            " WHERE archived = 1 ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [_serialize_row(r) for r in rows]

    async def unread_count(self) -> int:
        async with self._db.execute(
            "SELECT COUNT(*) FROM notifications WHERE read = 0 AND archived = 0"
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else 0

    async def mark_read(self, notif_id: int) -> None:
        await self._db.execute("UPDATE notifications SET read = 1 WHERE id = ?", (notif_id,))
        await self._db.commit()

    async def archive(self, notif_id: int) -> None:
        # Dismiss = archive. The row stays; the History view still shows it.
        await self._db.execute(
            "UPDATE notifications SET archived = 1 WHERE id = ?", (notif_id,)
        )
        await self._db.commit()

    async def archive_by_source_ref(self, source: str, request_id) -> int:
        """Archive active notifications whose JSON `data.request_id` matches.

        Used to retire an agent auth-request notification once the request is
        terminally decided (approved/denied) so it leaves the active bell list
        and moves into History. Acting on it both reads and archives the row
        (#62: nothing is deleted). Idempotent: rows already archived are
        skipped; returns the number newly archived.
        """
        async with self._db.execute(
            "SELECT id, data FROM notifications WHERE source = ? AND archived = 0",
            (source,),
        ) as cursor:
            rows = await cursor.fetchall()
        target = str(request_id)
        ids = []
        for nid, raw in rows:
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except (ValueError, TypeError):
                continue
            if str(payload.get("request_id")) == target:
                ids.append(nid)
        if ids:
            placeholders = ",".join("?" * len(ids))
            await self._db.execute(
                f"UPDATE notifications SET archived = 1, read = 1 WHERE id IN ({placeholders})",
                ids,
            )
            await self._db.commit()
        return len(ids)

    async def mark_all_read(self) -> int:
        cursor = await self._db.execute("UPDATE notifications SET read = 1 WHERE read = 0")
        await self._db.commit()
        return cursor.rowcount

    async def cleanup(self, max_age_days: int = 30) -> int:
        # Age out only old UNdismissed notifications. Archived rows are the
        # durable history a user explicitly dismissed (#62 / append-only #103),
        # so they are never GC'd here.
        cutoff = int(time.time()) - (max_age_days * 86400)
        cursor = await self._db.execute(
            "DELETE FROM notifications WHERE timestamp < ? AND archived = 0", (cutoff,)
        )
        await self._db.commit()
        return cursor.rowcount

    async def emit_event(self, event_type: str, title: str, message: str, level: str = "info") -> None:
        if await self._is_event_muted(event_type):
            return
        await self.add(title, message, level=level, source=event_type)

    async def _is_event_muted(self, event_type: str) -> bool:
        async with self._db.execute(
            "SELECT muted FROM notification_prefs WHERE event_type = ?", (event_type,)
        ) as cursor:
            row = await cursor.fetchone()
        return bool(row[0]) if row else False

    async def set_event_muted(self, event_type: str, muted: bool) -> None:
        await self._db.execute(
            "INSERT OR REPLACE INTO notification_prefs (event_type, muted) VALUES (?, ?)",
            (event_type, int(muted)),
        )
        await self._db.commit()

    async def get_event_prefs(self) -> list[dict]:
        async with self._db.execute(
            "SELECT event_type, muted FROM notification_prefs"
        ) as cursor:
            rows = await cursor.fetchall()
        stored = {r[0]: bool(r[1]) for r in rows}
        return [
            {"event_type": et, "muted": stored.get(et, False)}
            for et in self.EVENT_TYPES
        ]
