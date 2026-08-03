from __future__ import annotations

import time

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

LISTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_lists (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active',
    created_by TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lists_project ON project_lists(project_id);
CREATE INDEX IF NOT EXISTS idx_lists_status ON project_lists(project_id, status);
"""


class ProjectListsStore(BaseStore):
    SCHEMA = LISTS_SCHEMA

    async def create_list(
        self,
        project_id: str,
        title: str,
        created_by: str,
        description: str = "",
        status: str = "active",
    ) -> dict:
        list_id = new_id("lst")
        now = time.time()
        await self._db.execute(
            "INSERT INTO project_lists "
            "(id, project_id, title, description, status, created_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (list_id, project_id, title, description, status, created_by, now, now),
        )
        await self._db.commit()
        return await self.get_list(list_id)

    async def get_list(self, list_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM project_lists WHERE id = ?", (list_id,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            keys = [d[0] for d in cur.description]
            return dict(zip(keys, row))

    async def list_lists(self, project_id: str) -> list[dict]:
        async with self._db.execute(
            "SELECT * FROM project_lists WHERE project_id = ? ORDER BY created_at ASC",
            (project_id,),
        ) as cur:
            rows = await cur.fetchall()
            keys = [d[0] for d in cur.description]
            return [dict(zip(keys, r)) for r in rows]

    async def update_list(
        self,
        list_id: str,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
    ) -> dict | None:
        sets = []
        params = []
        if title is not None:
            sets.append("title = ?")
            params.append(title)
        if description is not None:
            sets.append("description = ?")
            params.append(description)
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if not sets:
            return await self.get_list(list_id)
        sets.append("updated_at = ?")
        params.append(time.time())
        params.append(list_id)
        await self._db.execute(
            f"UPDATE project_lists SET {', '.join(sets)} WHERE id = ?", params
        )
        await self._db.commit()
        return await self.get_list(list_id)

    async def delete_list(self, list_id: str) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM project_lists WHERE id = ?", (list_id,)
        )
        await self._db.commit()
        return cursor.rowcount > 0


class ProjectListEntriesStore(BaseStore):
    SCHEMA = """
    CREATE TABLE IF NOT EXISTS project_list_entries (
        id TEXT PRIMARY KEY,
        list_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        text TEXT NOT NULL,
        original_text TEXT NOT NULL,
        category TEXT,
        status TEXT NOT NULL DEFAULT 'new',
        done INTEGER NOT NULL DEFAULT 0,
        author_kind TEXT NOT NULL,
        author_id TEXT NOT NULL,
        edited_by TEXT,
        position INTEGER NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_entries_list ON project_list_entries(list_id);
    CREATE INDEX IF NOT EXISTS idx_entries_project ON project_list_entries(project_id);
    CREATE INDEX IF NOT EXISTS idx_entries_list_project ON project_list_entries(list_id, project_id);
    CREATE INDEX IF NOT EXISTS idx_entries_status ON project_list_entries(project_id, status);
    """
    MIGRATIONS = []

    async def add_entry(
        self,
        list_id: str,
        project_id: str,
        text: str,
        original_text: str,
        author_kind: str,
        author_id: str,
        category: str | None = None,
        position: int | None = None,
    ) -> dict:
        entry_id = new_id("ent")
        now = time.time()

        if position is not None:
            await self._db.execute(
                "INSERT INTO project_list_entries "
                "(id, list_id, project_id, text, original_text, category, status, "
                "done, author_kind, author_id, position, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entry_id, list_id, project_id, text, original_text,
                    category, "new", 0, author_kind, author_id,
                    position, now, now,
                ),
            )
        else:
            await self._db.execute(
                "INSERT INTO project_list_entries "
                "(id, list_id, project_id, text, original_text, category, status, "
                "done, author_kind, author_id, position, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                "COALESCE((SELECT MAX(position) + 1 FROM project_list_entries "
                "WHERE list_id = ? AND project_id = ?), 0), ?, ?)",
                (
                    entry_id, list_id, project_id, text, original_text,
                    category, "new", 0, author_kind, author_id,
                    list_id, project_id, now, now,
                ),
            )
        await self._db.commit()
        return await self.get_entry(entry_id)

    async def get_entry(self, entry_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM project_list_entries WHERE id = ?", (entry_id,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
        keys = [d[0] for d in cur.description]
        return dict(zip(keys, row))

    async def list_entries(
        self,
        project_id: str,
        list_id: str | None = None,
        status: str | None = None,
        category: str | None = None,
    ) -> list[dict]:
        where_parts = ["project_id = ?"]
        params: list = [project_id]

        if list_id is not None:
            where_parts.append("list_id = ?")
            params.append(list_id)
        if status is not None:
            where_parts.append("status = ?")
            params.append(status)
        if category is not None:
            where_parts.append("category = ?")
            params.append(category)

        async with self._db.execute(
            f"SELECT * FROM project_list_entries WHERE {' AND '.join(where_parts)} "
            "ORDER BY position ASC, created_at ASC",
            params,
        ) as cur:
            rows = await cur.fetchall()
            keys = [d[0] for d in cur.description]
            return [dict(zip(keys, r)) for r in rows]

    async def update_entry(
        self,
        entry_id: str,
        text: str | None = None,
        category: str | None = None,
        status: str | None = None,
        done: int | None = None,
        position: int | None = None,
        edited_by: str | None = None,
    ) -> dict | None:
        sets = []
        params = []
        if text is not None:
            sets.append("text = ?")
            params.append(text)
        if category is not None:
            sets.append("category = ?")
            params.append(category)
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if done is not None:
            sets.append("done = ?")
            params.append(done)
        if position is not None:
            sets.append("position = ?")
            params.append(position)
        if edited_by is not None:
            sets.append("edited_by = ?")
            params.append(edited_by)
        if not sets:
            return await self.get_entry(entry_id)
        existing = await self.get_entry(entry_id)
        if existing is None:
            return None
        sets.append("updated_at = ?")
        params.append(time.time())
        params.append(entry_id)
        await self._db.execute(
            f"UPDATE project_list_entries SET {', '.join(sets)} WHERE id = ?",
            params,
        )
        await self._db.commit()
        return await self.get_entry(entry_id)

    async def delete_entry(self, entry_id: str) -> bool:
        cursor = await self._db.execute(
            "DELETE FROM project_list_entries WHERE id = ?", (entry_id,)
        )
        await self._db.commit()
        return cursor.rowcount == 1

    async def reorder_entries(self, project_id: str, list_id: str, entries: list[dict]) -> bool:
        for entry in entries:
            cursor = await self._db.execute(
                "UPDATE project_list_entries SET position = ?, updated_at = ? "
                "WHERE id = ? AND project_id = ? AND list_id = ?",
                (entry["position"], time.time(), entry["id"], project_id, list_id),
            )
            if cursor.rowcount == 0:
                await self._db.rollback()
                return False
        await self._db.commit()
        return True

    async def _get_next_position(self, project_id: str, list_id: str) -> int:
        async with self._db.execute(
            "SELECT MAX(position) + 1 FROM project_list_entries "
            "WHERE project_id = ? AND list_id = ?",
            (project_id, list_id),
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row[0] is not None else 0
