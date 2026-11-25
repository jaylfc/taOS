from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

# Maximum allowed screenshot size (base64 data-URL string length, not raw bytes).
# A 2 MB raw image becomes ~2.73 MB as base64; 4 MB covers that with headroom.
MAX_SCREENSHOT_LEN = 4_000_000

# Maximum body text length.
MAX_BODY_LEN = 20_000

# Maximum feedback submissions per user in a 24-hour window.
MAX_FEEDBACK_PER_USER_PER_DAY = 20

# The install's checkout root (where tinyagentos/ lives one level down) --
# the same PROJECT_DIR app.py computes, derived here so the store does not
# import the app module.
_PROJECT_DIR = Path(__file__).resolve().parent.parent


def _parse_context(raw: Any) -> dict:
    """Decode a stored context blob; anything unusable reads back as {}."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


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
        context TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS feedback_user_created
        ON feedback (user_id, created_at DESC);
    """

    def __init__(
        self,
        db_path: Path,
        *,
        context_provider: Optional[Callable[[], dict]] = None,
    ):
        super().__init__(db_path)
        # #777 Layer 1: every submission auto-attaches the local install
        # context (version/commit, install identity, update history). The
        # provider is injectable for tests; the default snapshots the
        # install this store serves -- its data dir is the db's parent.
        self._context_provider = context_provider

    async def _post_init(self) -> None:
        # Databases created before #777 have no context column. Guarded
        # ALTER (PRAGMA check first): SCHEMA's CREATE TABLE covers fresh
        # databases, this covers existing ones, and the pair is what the
        # schema-column guard requires for a column added after release.
        assert self._db is not None
        cursor = await self._db.execute("PRAGMA table_info(feedback)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "context" not in cols:
            await self._db.execute(
                "ALTER TABLE feedback ADD COLUMN context TEXT NOT NULL DEFAULT ''"
            )
            await self._db.commit()

    def _collect_context(self) -> dict:
        """Snapshot the install context for one submission; never raises.

        A feedback submission must not fail because an identity or history
        source is unavailable, so any provider failure degrades to {}.
        """
        try:
            if self._context_provider is not None:
                provided = self._context_provider()
            else:
                from tinyagentos.feedback_context import collect_feedback_context

                provided = collect_feedback_context(
                    self.db_path.parent, _PROJECT_DIR
                )
        except Exception:
            logger.debug("feedback: context collection failed", exc_info=True)
            return {}
        return provided if isinstance(provided, dict) else {}

    async def _collect_context_async(self) -> dict:
        """Collect the submission context off the event loop.

        ``_collect_context`` is synchronous and, on the default path,
        shells out to ``git rev-parse`` with a 5 s timeout; called inline
        from the async create paths it would stall every other request
        for that long (a slow or hung git blocks the loop). Run it in a
        worker thread instead. The degradation contract is unchanged --
        ``_collect_context`` already turns any provider failure into {}
        and never raises.
        """
        return await asyncio.to_thread(self._collect_context)

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
        context = await self._collect_context_async()
        await self._db.execute(
            """
            INSERT INTO feedback (id, user_id, type, title, body, screenshot, app, context, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item_id, user_id, type, title, body, screenshot, app,
                json.dumps(context), created_at,
            ),
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
            "context": context,
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
        context = await self._collect_context_async()
        cursor = await self._db.execute(
            """
            INSERT INTO feedback (id, user_id, type, title, body, screenshot, app, context, created_at)
            SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?
            WHERE (
                SELECT COUNT(*) FROM feedback WHERE user_id = ? AND created_at >= ?
            ) < ?
            """,
            (
                item_id, user_id, type, title, body, screenshot, app,
                json.dumps(context), created_at,
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
            "context": context,
            "created_at": created_at,
        }

    async def list_for_user(self, user_id: str) -> list[dict]:
        """Return all submissions for a user, most recent first, without screenshot blobs."""
        assert self._db is not None
        cursor = await self._db.execute(
            """
            SELECT id, user_id, type, title, body, app, context, created_at,
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
                "context": _parse_context(r[6]),
                "created_at": r[7],
                "has_screenshot": bool(r[8]),
            }
            for r in rows
        ]

    async def get_by_id(self, item_id: str, user_id: str) -> dict | None:
        """Return a single feedback item including the screenshot, scoped to the user."""
        assert self._db is not None
        cursor = await self._db.execute(
            """
            SELECT id, user_id, type, title, body, screenshot, app, context, created_at
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
            "context": _parse_context(row[7]),
            "created_at": row[8],
            "has_screenshot": bool(row[5]),
        }
