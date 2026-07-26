from __future__ import annotations

import time

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

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
    MIGRATIONS = []  # No migrations needed per spec

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
        entry_id = new_id("lle")
        now = time.time()
        
        # Store immutability: original_text cannot be changed after creation
        # It is stored verbatim as the first written text
        await self._db.execute(
            "INSERT INTO project_list_entries (
                id, list_id, project_id, text, original_text, category, status, 
                done, author_kind, author_id, position, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id, list_id, project_id, text, original_text, 
                category, "new", 0, author_kind, author_id, 
                position if position is not None else 0, now, now,
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
        where_clause = "project_id = ?"
        params = [project_id]
        
        if list_id is not None:
            where_clause += " AND list_id = ?"
            params.append(list_id)
        
        if status is not None:
            where_clause += " AND status = ?"
            params.append(status)
        
        if category is not None:
            where_clause += " AND category = ?"
            params.append(category)
        
        async with self._db.execute(
            f"SELECT * FROM project_list_entries WHERE {where_clause} ORDER BY position ASC, created_at ASC",
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
        # Enforce immutability: original_text cannot be changed
        # Only text, category, status, done, position can be updated
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
        
        sets.append("updated_at = ?")
        params.append(time.time())
        params.append(entry_id)
        
        # Before updating, verify that original_text cannot be modified
        existing = await self.get_entry(entry_id)
        if existing is None:
            return None
            
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

    async def reorder_entries(self, project_id: str, entries: list[dict]) -> None:
        for entry in entries:
            await self._db.execute(
                "UPDATE project_list_entries SET position = ?, updated_at = ? WHERE id = ? AND project_id = ?",
                (entry["position"], time.time(), entry["id"], project_id),
            )
        await self._db.commit()

    async def _get_next_position(self, project_id: str, list_id: str | None = None) -> int:
        async with self._db.execute(
            "SELECT MAX(position) + 1 FROM project_list_entries WHERE project_id = ? AND list_id = ?",
            (project_id, list_id or ""),
        ) as cur:
            row = await cur.fetchone()
            return (row[0] if row[0] is not None else 0)
