from __future__ import annotations

import time
import uuid
from pathlib import Path

from tinyagentos.base_store import BaseStore

CANVAS_SCHEMA = """
CREATE TABLE IF NOT EXISTS canvases (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'Untitled',
    content TEXT NOT NULL DEFAULT '',
    style TEXT NOT NULL DEFAULT 'auto',
    format TEXT NOT NULL DEFAULT 'markdown',
    created_by TEXT NOT NULL,
    edit_token TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


def _row_to_dict(row: tuple, description) -> dict:
    keys = [d[0] for d in description]
    return dict(zip(keys, row))


class CanvasStore(BaseStore):
    SCHEMA = CANVAS_SCHEMA

    async def create(
        self,
        title: str = "Untitled",
        content: str = "",
        style: str = "auto",
        format: str = "markdown",
        created_by: str = "system",
    ) -> dict:
        canvas_id = uuid.uuid4().hex[:8]
        edit_token = uuid.uuid4().hex[:16]
        now = time.time()
        await self._db.execute(
            """
            INSERT INTO canvases (id, title, content, style, format, created_by, edit_token, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (canvas_id, title, content, style, format, created_by, edit_token, now, now),
        )
        await self._db.commit()
        return {
            "id": canvas_id,
            "title": title,
            "content": content,
            "style": style,
            "format": format,
            "created_by": created_by,
            "edit_token": edit_token,
            "created_at": now,
            "updated_at": now,
        }

    async def get(self, canvas_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM canvases WHERE id = ?", (canvas_id,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return _row_to_dict(row, cur.description)

    async def update(
        self,
        canvas_id: str,
        edit_token: str,
        content: str | None = None,
        title: str | None = None,
    ) -> bool:
        async with self._db.execute(
            "SELECT edit_token FROM canvases WHERE id = ?", (canvas_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None or row[0] != edit_token:
            return False

        fields = []
        values = []
        if content is not None:
            fields.append("content = ?")
            values.append(content)
        if title is not None:
            fields.append("title = ?")
            values.append(title)

        if not fields:
            return True  # nothing to update but token was valid

        fields.append("updated_at = ?")
        values.append(time.time())
        values.append(canvas_id)

        await self._db.execute(
            f"UPDATE canvases SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        await self._db.commit()
        return True

    async def delete(self, canvas_id: str) -> bool:
        async with self._db.execute(
            "SELECT id FROM canvases WHERE id = ?", (canvas_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return False
        await self._db.execute("DELETE FROM canvases WHERE id = ?", (canvas_id,))
        await self._db.commit()
        return True

    async def list_all(self, limit: int = 50) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM canvases ORDER BY updated_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_dict(row, desc) for row in rows]
