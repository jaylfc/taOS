"""Server-side capture of client (browser/PWA) logs and crashes.

In a PWA there is no devtools console for the user (or us) to read, so a
front-end crash is invisible unless the client ships it somewhere. This store is
that sink: the desktop posts errors, warnings, and debug lines to
POST /api/client-logs and they land here, readable by an admin via
GET /api/client-logs. It is the substrate for chasing crashes like the Messages
app failure (#106 log capture).

Bounded by design: a crash loop must not grow the table without limit, so every
insert prunes to the most recent MAX_ROWS rows (a ring buffer), and message/stack
text is length-capped.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tinyagentos.base_store import BaseStore

# The levels a client may report. Mirrors console severities plus an explicit
# "fatal" for an uncaught error / error-boundary crash.
VALID_LEVELS = frozenset({"fatal", "error", "warn", "info", "debug"})

MAX_MESSAGE_LEN = 4_000
MAX_STACK_LEN = 16_000
MAX_SOURCE_LEN = 200
MAX_URL_LEN = 1_000
MAX_UA_LEN = 500
# Ring-buffer cap: keep only the most recent N entries across all users so a
# crash loop posting on every render cannot grow the DB unbounded.
MAX_ROWS = 2_000


class ClientLogStore(BaseStore):
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS client_logs (
        id TEXT NOT NULL PRIMARY KEY,
        user_id TEXT NOT NULL,
        level TEXT NOT NULL,
        message TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        stack TEXT NOT NULL DEFAULT '',
        user_agent TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS client_logs_created
        ON client_logs (created_at DESC);
    CREATE INDEX IF NOT EXISTS client_logs_level_created
        ON client_logs (level, created_at DESC);
    """

    async def create(
        self,
        *,
        user_id: str,
        level: str,
        message: str,
        source: str = "",
        url: str = "",
        stack: str = "",
        user_agent: str = "",
    ) -> dict:
        assert self._db is not None
        item_id = str(uuid.uuid4())
        created_at = datetime.now(timezone.utc).isoformat()
        row = {
            "id": item_id,
            "user_id": user_id,
            "level": level,
            "message": message[:MAX_MESSAGE_LEN],
            "source": source[:MAX_SOURCE_LEN],
            "url": url[:MAX_URL_LEN],
            "stack": stack[:MAX_STACK_LEN],
            "user_agent": user_agent[:MAX_UA_LEN],
            "created_at": created_at,
        }
        await self._db.execute(
            """
            INSERT INTO client_logs
                (id, user_id, level, message, source, url, stack, user_agent, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                row["id"], row["user_id"], row["level"], row["message"],
                row["source"], row["url"], row["stack"], row["user_agent"],
                row["created_at"],
            ),
        )
        # Ring-buffer prune: keep only the most recently inserted MAX_ROWS rows.
        # Retain by rowid (monotonic insert order), not created_at: the timestamp
        # is a coarse ISO string, so same-microsecond rows under a crash loop tie
        # and make the prune non-deterministic. rowid is the indexed primary key.
        # Keep by RANK (the newest MAX_ROWS rowids), not a value threshold of
        # MAX(rowid) - MAX_ROWS: rowids are not contiguous once any row is deleted
        # out of band (per-user eviction, rolled-back inserts), so a value
        # threshold would retain FEWER than MAX_ROWS rows whenever gaps exist. The
        # subquery is index-ordered + capped, and the table itself stays bounded
        # near MAX_ROWS, so the NOT IN check is cheap.
        await self._db.execute(
            "DELETE FROM client_logs WHERE rowid NOT IN "
            "(SELECT rowid FROM client_logs ORDER BY rowid DESC LIMIT ?)",
            (MAX_ROWS,),
        )
        await self._db.commit()
        return row

    async def list_recent(
        self, *, level: str | None = None, limit: int = 200
    ) -> list[dict]:
        """Most recent entries first, optionally filtered by level. Admin-read."""
        assert self._db is not None
        limit = max(1, min(limit, 1000))
        cols = "id, user_id, level, message, source, url, stack, user_agent, created_at"
        if level:
            cursor = await self._db.execute(
                f"SELECT {cols} FROM client_logs WHERE level = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (level, limit),
            )
        else:
            cursor = await self._db.execute(
                f"SELECT {cols} FROM client_logs ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        keys = cols.split(", ")
        return [dict(zip(keys, r)) for r in rows]
