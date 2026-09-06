from __future__ import annotations

import logging
import secrets
import time
from pathlib import Path

import aiosqlite

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

WEB_SITES_SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


def _new_site_id() -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"site-{suffix}"


async def _migration_add_index_html(conn: aiosqlite.Connection) -> None:
    """Add the `index_html` column (idempotent). Added after initial release,
    so existing databases must be migrated rather than assumed to have it.

    This deliberately uses a guarded _post_init (PRAGMA table_info check +
    conditional ALTER, committing its own change) rather than the MIGRATIONS
    list: db_migrations.py footgun #2 documents that the MIGRATIONS runner
    baseline-stamps pre-existing DBs at the latest version WITHOUT executing
    any SQL, so a retrofit column added there would be silently skipped on
    exactly the legacy rows that need it. The manual commit here is the same
    sanctioned pattern as agent_registry_store._migration_v1_add_status and
    knowledge_store's user_id retrofit, and is safely idempotent: the
    ALTER only runs when the column is absent."""
    existing_cols = {row[1] for row in await (await conn.execute("PRAGMA table_info(sites)")).fetchall()}
    if "index_html" not in existing_cols:
        await conn.execute("ALTER TABLE sites ADD COLUMN index_html TEXT NOT NULL DEFAULT ''")
        await conn.commit()


class WebSiteStore(BaseStore):
    """Persists website-builder documents.

    ``content`` holds the site model serialized as JSON -- the editable
    source of truth; the store treats it as an opaque TEXT blob, validation
    of the model lives in the frontend. ``index_html`` holds the exported,
    self-contained static HTML rendered from that model (export.ts's
    exportSiteHtml), a derived artifact kept alongside it so the preview and
    package routes have something servable without re-rendering client-side.
    """

    SCHEMA = WEB_SITES_SCHEMA

    def __init__(self, db_path: Path):
        super().__init__(db_path)

    async def _post_init(self) -> None:
        await _migration_add_index_html(self._db)

    async def create(self, title: str, content: str, index_html: str = "") -> dict:
        now = int(time.time())
        # The id is generated and inserted speculatively; the PRIMARY KEY
        # constraint is the actual source of truth for uniqueness, not a
        # prior SELECT (which would leave a TOCTOU window between two
        # concurrent creates picking the same id). On a collision we just
        # retry with a fresh id, bounded so a persistently broken generator
        # can't spin forever.
        for _ in range(8):
            site_id = _new_site_id()
            row = {
                "id": site_id,
                "title": title,
                "content": content,
                "index_html": index_html,
                "created_at": now,
                "updated_at": now,
            }
            try:
                await self._db.execute(
                    """INSERT INTO sites (id, title, content, index_html, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        row["id"],
                        row["title"],
                        row["content"],
                        row["index_html"],
                        row["created_at"],
                        row["updated_at"],
                    ),
                )
                await self._db.commit()
                return row
            except aiosqlite.IntegrityError:
                # id collision: log it (a persistent stream would point at an
                # entropy regression) and retry with a fresh id.
                logger.warning("web_sites: site id collision on %s, retrying", site_id)
                continue
        raise RuntimeError("could not allocate site id after repeated id collisions")

    async def list(self) -> list[dict]:
        async with self._db.execute(
            "SELECT id, title, created_at, updated_at FROM sites ORDER BY updated_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [
            {"id": r[0], "title": r[1], "created_at": r[2], "updated_at": r[3]}
            for r in rows
        ]

    async def get(self, site_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT id, title, content, index_html, created_at, updated_at FROM sites WHERE id = ?",
            (site_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "title": row[1],
            "content": row[2],
            "index_html": row[3],
            "created_at": row[4],
            "updated_at": row[5],
        }

    async def update(self, site_id: str, title: str, content: str, index_html: str = "") -> dict | None:
        now = int(time.time())
        await self._db.execute(
            "UPDATE sites SET title = ?, content = ?, index_html = ?, updated_at = ? WHERE id = ?",
            (title, content, index_html, now, site_id),
        )
        await self._db.commit()
        return await self.get(site_id)

    async def delete(self, site_id: str) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM sites WHERE id = ?", (site_id,)
        ) as cur:
            exists = await cur.fetchone() is not None
        if not exists:
            return False
        await self._db.execute("DELETE FROM sites WHERE id = ?", (site_id,))
        await self._db.commit()
        return True
