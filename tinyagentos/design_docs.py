from __future__ import annotations

import logging
import secrets
import time
from pathlib import Path

import aiosqlite

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

DESIGN_DOCS_SCHEMA = """
CREATE TABLE IF NOT EXISTS designs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


def _new_design_id() -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"design-{suffix}"


class DesignStore(BaseStore):
    """Persists Design Studio documents.

    ``content`` holds the design's artboard + canvas elements serialized as
    JSON. The store treats it as an opaque TEXT blob; validation of the
    model lives in the frontend.
    """

    SCHEMA = DESIGN_DOCS_SCHEMA

    def __init__(self, db_path: Path):
        super().__init__(db_path)

    async def create(self, name: str, content: str) -> dict:
        now = int(time.time())
        # The id is generated and inserted speculatively; the PRIMARY KEY
        # constraint is the actual source of truth for uniqueness, not a
        # prior SELECT (which would leave a TOCTOU window between two
        # concurrent creates picking the same id). On a collision we just
        # retry with a fresh id, bounded so a persistently broken generator
        # can't spin forever.
        for _ in range(8):
            design_id = _new_design_id()
            row = {
                "id": design_id,
                "name": name,
                "content": content,
                "created_at": now,
                "updated_at": now,
            }
            try:
                await self._db.execute(
                    """INSERT INTO designs (id, name, content, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?)""",
                    (row["id"], row["name"], row["content"], row["created_at"], row["updated_at"]),
                )
                await self._db.commit()
                return row
            except aiosqlite.IntegrityError:
                # id collision: log it (a persistent stream would point at an
                # entropy regression) and retry with a fresh id.
                logger.warning("design_docs: design id collision on %s, retrying", design_id)
                continue
        raise RuntimeError("could not allocate design id after repeated id collisions")

    async def list(self) -> list[dict]:
        async with self._db.execute(
            "SELECT id, name, created_at, updated_at FROM designs ORDER BY updated_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [
            {"id": r[0], "name": r[1], "created_at": r[2], "updated_at": r[3]}
            for r in rows
        ]

    async def get(self, design_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT id, name, content, created_at, updated_at FROM designs WHERE id = ?",
            (design_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "name": row[1],
            "content": row[2],
            "created_at": row[3],
            "updated_at": row[4],
        }

    async def update(self, design_id: str, name: str, content: str) -> dict | None:
        now = int(time.time())
        await self._db.execute(
            "UPDATE designs SET name = ?, content = ?, updated_at = ? WHERE id = ?",
            (name, content, now, design_id),
        )
        await self._db.commit()
        return await self.get(design_id)

    async def delete(self, design_id: str) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM designs WHERE id = ?", (design_id,)
        ) as cur:
            exists = await cur.fetchone() is not None
        if not exists:
            return False
        await self._db.execute("DELETE FROM designs WHERE id = ?", (design_id,))
        await self._db.commit()
        return True
