from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tinyagentos.base_store import BaseStore

# Maximum allowed screenshot size (base64 data-URL string length, not raw bytes).
# A 2 MB raw image becomes ~2.73 MB as base64; 4 MB covers that with headroom.
MAX_SCREENSHOT_LEN = 4_000_000

# Maximum body text length.
MAX_BODY_LEN = 20_000

# Maximum feedback submissions per user in a 24-hour window.
MAX_FEEDBACK_PER_USER_PER_DAY = 20


class FeedbackStore(BaseStore):
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS feedback (
        id TEXT NOT NULL PRIMARY KEY,
        user_id TEXT NOT NULL,
        type TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL DEFAULT '',
        screenshot TEXT NOT NULL DEFAULT '',
        app TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS feedback_user_created
        ON feedback (user_id, created_at DESC);
    """

    async def create(
        self,
        *,
        user_id: str,
        type: str,
        title: str,
        body: str,
        screenshot: str = "",
        app: str = "",
    ) -> dict:
        assert self._db is not None
        item_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT INTO feedback (id, user_id, type, title, body, screenshot, app, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (item_id, user_id, type, title, body, screenshot, app, created_at),
        )
        await self._db.commit()
        return {
            "id": item_id,
            "user_id": user_id,
            "type": type,
            "title": title,
            "body": body,
            "screenshot": screenshot,
            "app": app,
            "created_at": created_at,
        }

    async def create_within_cap(
        self,
        *,
        user_id: str,
        type: str,
        title: str,
        body: str,
        screenshot: str = "",
        app: str = "",
        since: str,
        cap: int,
    ) -> dict | None:
        """Insert only if the user is under `cap` submissions since `since`.

        Returns the created row, or None if the cap was already reached.

        Counting and inserting as two calls looks correct and is not: two
        concurrent requests both read the same count, both see room, and both
        insert, so the cap is exceeded by however many requests raced. A user
        with several tabs open hits this without trying, and a scripted client
        defeats the limit entirely.

        One INSERT ... SELECT ... WHERE (SELECT COUNT(*) ...) < ? evaluates the
        count and performs the write inside a single statement, so SQLite's
        write lock makes the check-and-insert indivisible. rowcount tells us
        which way it went.
        """
        assert self._db is not None
        item_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            """
            INSERT INTO feedback (id, user_id, type, title, body, screenshot, app, created_at)
            SELECT ?, ?, ?, ?, ?, ?, ?, ?
            WHERE (
                SELECT COUNT(*) FROM feedback WHERE user_id = ? AND created_at >= ?
            ) < ?
            """,
            (
                item_id, user_id, type, title, body, screenshot, app, created_at,
                user_id, since, cap,
            ),
        )
        if not cursor.rowcount:
            await self._db.rollback()
            return None
        await self._db.commit()
        return {
            "id": item_id,
            "user_id": user_id,
            "type": type,
            "title": title,
            "body": body,
            "screenshot": screenshot,
            "app": app,
            "created_at": created_at,
        }

    async def list_for_user(self, user_id: str) -> list[dict]:
        """Return all submissions for a user, most recent first, without screenshot blobs."""
        assert self._db is not None
        cursor = await self._db.execute(
            """
            SELECT id, user_id, type, title, body, app, created_at,
                   (screenshot != '') AS has_screenshot
            FROM feedback
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "user_id": r[1],
                "type": r[2],
                "title": r[3],
                "body": r[4],
                "app": r[5],
                "created_at": r[6],
                "has_screenshot": bool(r[7]),
            }
            for r in rows
        ]

    async def get_by_id(self, item_id: str, user_id: str) -> dict | None:
        """Return a single feedback item including the screenshot, scoped to the user."""
        assert self._db is not None
        cursor = await self._db.execute(
            """
            SELECT id, user_id, type, title, body, screenshot, app, created_at
            FROM feedback
            WHERE id = ? AND user_id = ?
            """,
            (item_id, user_id),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "user_id": row[1],
            "type": row[2],
            "title": row[3],
            "body": row[4],
            "screenshot": row[5],
            "app": row[6],
            "created_at": row[7],
            "has_screenshot": bool(row[5]),
        }
