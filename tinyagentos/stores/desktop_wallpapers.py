from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from tinyagentos.base_store import BaseStore


class DesktopWallpapersStore(BaseStore):
    """Store for user-uploaded desktop wallpapers.

    Each wallpaper has a unique id, a display label (original filename),
    the on-disk filename, MIME type, and creation timestamp.  The actual
    image bytes live under ``wallpapers_dir``, keyed by ``id.ext``.
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS user_wallpapers (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT '',
        label TEXT NOT NULL,
        filename TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    """

    def __init__(self, db_path: Path, wallpapers_dir: Path):
        super().__init__(db_path)
        self.wallpapers_dir = wallpapers_dir

    async def init(self) -> None:
        self.wallpapers_dir.mkdir(parents=True, exist_ok=True)
        await super().init()

    async def add_wallpaper(
        self, label: str, filename: str, mime_type: str, user_id: str = "",
    ) -> dict:
        """Persist a new wallpaper record and return its metadata.

        The caller is responsible for writing the image file to
        ``wallpapers_dir / id.ext`` **before** calling this method.
        """
        assert self._db is not None
        wp_id = uuid.uuid4().hex
        created_at = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            "INSERT INTO user_wallpapers (id, user_id, label, filename, mime_type, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (wp_id, user_id, label, filename, mime_type, created_at),
        )
        await self._db.commit()
        return {
            "id": wp_id,
            "label": label,
            "filename": filename,
            "mime_type": mime_type,
            "url": f"/api/desktop/wallpapers/{wp_id}",
            "created_at": created_at,
        }

    async def list_wallpapers(self, user_id: str = "") -> list[dict]:
        """Return all user-uploaded wallpapers, newest first."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT id, label, filename, mime_type, created_at "
            "FROM user_wallpapers WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row[0],
                "label": row[1],
                "filename": row[2],
                "mime_type": row[3],
                "url": f"/api/desktop/wallpapers/{row[0]}",
                "created_at": row[4],
            }
            for row in rows
        ]

    async def get_wallpaper(self, wp_id: str) -> dict | None:
        """Get a single wallpaper by id."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT id, user_id, label, filename, mime_type, created_at "
            "FROM user_wallpapers WHERE id = ?",
            (wp_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "user_id": row[1],
            "label": row[2],
            "filename": row[3],
            "mime_type": row[4],
            "url": f"/api/desktop/wallpapers/{row[0]}",
            "created_at": row[5],
        }

    async def delete_wallpaper(self, wp_id: str) -> bool:
        """Delete a wallpaper record.  Returns True if a row was deleted.

        The caller is responsible for removing the on-disk file.
        """
        assert self._db is not None
        cursor = await self._db.execute(
            "DELETE FROM user_wallpapers WHERE id = ?", (wp_id,)
        )
        await self._db.commit()
        return cursor.rowcount > 0
