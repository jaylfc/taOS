from __future__ import annotations

import time

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

NOTES_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_notes (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    author_id TEXT NOT NULL,
    author_kind TEXT NOT NULL DEFAULT 'user',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_project ON project_notes(project_id);
CREATE INDEX IF NOT EXISTS idx_notes_project_created ON project_notes(project_id, created_at DESC);
"""


class ProjectNotesStore(BaseStore):
    SCHEMA = NOTES_SCHEMA

    async def create_note(
        self,
        project_id: str,
        title: str,
        body: str,
        author_id: str,
        author_kind: str = "user",
    ) -> dict:
        note_id = new_id("note")
        now = time.time()
        await self._db.execute(
            """INSERT INTO project_notes
               (id, project_id, title, body, author_id, author_kind, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (note_id, project_id, title, body, author_id, author_kind, now, now),
        )
        await self._db.commit()
        return await self.get_note(note_id)

    async def get_note(self, note_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM project_notes WHERE id = ?", (note_id,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            keys = [d[0] for d in cur.description]
            return dict(zip(keys, row))

    async def list_notes(self, project_id: str) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM project_notes WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
            keys = [d[0] for d in cur.description]
            return [dict(zip(keys, r)) for r in rows]

    async def update_note(
        self,
        note_id: str,
        title: str | None = None,
        body: str | None = None,
    ) -> dict | None:
        sets: list[str] = []
        params: list = []
        if title is not None:
            sets.append("title = ?")
            params.append(title)
        if body is not None:
            sets.append("body = ?")
            params.append(body)
        if not sets:
            return await self.get_note(note_id)
        sets.append("updated_at = ?")
        params.append(time.time())
        params.append(note_id)
        await self._db.execute(
            f"UPDATE project_notes SET {', '.join(sets)} WHERE id = ?", params
        )
        await self._db.commit()
        return await self.get_note(note_id)

    async def delete_note(self, note_id: str) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM project_notes WHERE id = ?", (note_id,)
        )
        await self._db.commit()
        return cursor.rowcount > 0
