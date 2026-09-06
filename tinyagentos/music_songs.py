from __future__ import annotations

import secrets
import time
from pathlib import Path

from tinyagentos.base_store import BaseStore

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

SONGS_SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


def _new_song_id() -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"song-{suffix}"


class SongStore(BaseStore):
    """Persistence for Music Studio songs (Phase 1). Mirrors OfficeDocStore:
    `content` holds the song's tempo/key/timeSig/tracks as opaque JSON --
    the frontend (musicstudio/types.ts) owns that shape, not this store."""

    SCHEMA = SONGS_SCHEMA

    def __init__(self, db_path: Path):
        super().__init__(db_path)

    async def create(self, name: str, content: str) -> dict:
        for _ in range(8):
            song_id = _new_song_id()
            async with self._db.execute(
                "SELECT 1 FROM songs WHERE id = ?", (song_id,)
            ) as cur:
                if await cur.fetchone() is None:
                    break
        else:
            raise RuntimeError("could not allocate song id")

        now = int(time.time())
        row = {
            "id": song_id,
            "name": name,
            "content": content,
            "created_at": now,
            "updated_at": now,
        }
        await self._db.execute(
            """INSERT INTO songs (id, name, content, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (row["id"], row["name"], row["content"], row["created_at"], row["updated_at"]),
        )
        await self._db.commit()
        return row

    async def list(self) -> list[dict]:
        async with self._db.execute(
            "SELECT id, name, created_at, updated_at FROM songs ORDER BY updated_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [
            {"id": r[0], "name": r[1], "created_at": r[2], "updated_at": r[3]}
            for r in rows
        ]

    async def get(self, song_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT id, name, content, created_at, updated_at FROM songs WHERE id = ?",
            (song_id,),
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

    async def update(self, song_id: str, name: str, content: str) -> dict | None:
        # Use the affected rowcount to tell a real update from a missing row so
        # callers get None (-> 404) instead of a silent no-op that reads back as
        # a fresh SELECT.
        now = int(time.time())
        cursor = await self._db.execute(
            "UPDATE songs SET name = ?, content = ?, updated_at = ? WHERE id = ?",
            (name, content, now, song_id),
        )
        await self._db.commit()
        if cursor.rowcount == 0:
            return None
        return await self.get(song_id)

    async def delete(self, song_id: str) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM songs WHERE id = ?", (song_id,)
        ) as cur:
            exists = await cur.fetchone() is not None
        if not exists:
            return False
        await self._db.execute("DELETE FROM songs WHERE id = ?", (song_id,))
        await self._db.commit()
        return True
