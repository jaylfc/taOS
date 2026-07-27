"""SQLite-backed store for todo lists with checklist-style items.

Each list has ordered items with optional due dates and reminders.
Ownership and access control are kept simple: the creator owns the list
and is the only user who can modify it. Future work can add sharing
via the collaboration module once extracted from SharedDocsStore.
"""

from __future__ import annotations

import secrets
import time

from tinyagentos.base_store import BaseStore

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"


def _new_id(prefix: str) -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"{prefix}-{suffix}"


TODO_SCHEMA = """
CREATE TABLE IF NOT EXISTS todo_lists (
    id            TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    migrated_from TEXT,
    migration_complete INTEGER NOT NULL DEFAULT 0,
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    archived_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_todo_lists_owner
    ON todo_lists(owner_user_id, archived_at);

CREATE TABLE IF NOT EXISTS todo_items (
    id          TEXT PRIMARY KEY,
    list_id     TEXT NOT NULL REFERENCES todo_lists(id),
    text        TEXT NOT NULL DEFAULT '',
    done        INTEGER NOT NULL DEFAULT 0,
    position    INTEGER NOT NULL DEFAULT 0,
    due_at      REAL,
    remind_at   REAL,
    author      TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_todo_items_list
    ON todo_items(list_id, position);
CREATE UNIQUE INDEX IF NOT EXISTS idx_todo_lists_migrated_from
    ON todo_lists(migrated_from);
"""


def _row(cursor_desc, row) -> dict:
    return dict(zip([c[0] for c in cursor_desc], row))


_UNSET: object = object()


class TodoStore(BaseStore):
    SCHEMA = TODO_SCHEMA

    # ------------------------------------------------------------------ lists

    async def create_list(
        self, owner_user_id: str, title: str = "", migrated_from: str | None = None,
    ) -> dict:
        list_id = _new_id("tl")
        now = time.time()
        await self._db.execute(
            "INSERT INTO todo_lists "
            "(id, owner_user_id, title, migrated_from, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (list_id, owner_user_id, title, migrated_from, now, now),
        )
        await self._db.commit()
        return await self.get_list(list_id)

    async def list_lists(
        self, user_id: str, *, include_archived: bool = False
    ) -> list[dict]:
        q = (
            "SELECT * FROM todo_lists "
            "WHERE owner_user_id = ? "
        )
        if not include_archived:
            q += "AND archived_at IS NULL "
        q += "ORDER BY updated_at DESC"
        cur = await self._db.execute(q, (user_id,))
        rows = await cur.fetchall()
        return [_row(cur.description, r) for r in rows]

    async def get_list(self, list_id: str) -> dict | None:
        cur = await self._db.execute(
            "SELECT * FROM todo_lists WHERE id = ?", (list_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        doc = _row(cur.description, row)
        doc["items"] = await self.list_items(list_id)
        return doc

    async def set_title(self, list_id: str, title: str) -> None:
        await self._db.execute(
            "UPDATE todo_lists SET title = ?, updated_at = ? WHERE id = ?",
            (title, time.time(), list_id),
        )
        await self._db.commit()

    async def archive_list(self, list_id: str) -> None:
        await self._db.execute(
            "UPDATE todo_lists SET archived_at = ?, updated_at = ? WHERE id = ?",
            (time.time(), time.time(), list_id),
        )
        await self._db.commit()

    async def unarchive_list(self, list_id: str) -> None:
        await self._db.execute(
            "UPDATE todo_lists SET archived_at = NULL, updated_at = ? WHERE id = ?",
            (time.time(), list_id),
        )
        await self._db.commit()

    async def set_migrated_from(self, list_id: str, source_doc_id: str) -> None:
        await self._db.execute(
            "UPDATE todo_lists SET migrated_from = ?, updated_at = ? WHERE id = ?",
            (source_doc_id, time.time(), list_id),
        )
        await self._db.commit()

    async def set_migration_complete(self, list_id: str) -> None:
        await self._db.execute(
            "UPDATE todo_lists SET migration_complete = 1, updated_at = ? WHERE id = ?",
            (time.time(), list_id),
        )
        await self._db.commit()

    # ------------------------------------------------------------------ items

    async def add_item(
        self,
        list_id: str,
        text: str,
        author: str = "",
        due_at: float | None = None,
        remind_at: float | None = None,
    ) -> dict:
        text = text.strip()
        if not text:
            raise ValueError("Item text cannot be empty or whitespace-only")

        item_id = _new_id("ti")
        now = time.time()

        await self._db.execute(
            "INSERT INTO todo_items "
            "(id, list_id, text, done, position, due_at, remind_at, author, created_at, updated_at) "
            "SELECT ?, ?, ?, 0, COALESCE(MAX(position), -1) + 1, ?, ?, ?, ?, ? "
            "FROM todo_items WHERE list_id = ?",
            (item_id, list_id, text, due_at, remind_at, author, now, now, list_id),
        )
        await self._db.execute(
            "UPDATE todo_lists SET updated_at = ? WHERE id = ?", (now, list_id)
        )
        await self._db.commit()
        cur = await self._db.execute(
            "SELECT * FROM todo_items WHERE id = ?", (item_id,)
        )
        row = _row(cur.description, await cur.fetchone())
        row["done"] = bool(row["done"])
        return row

    async def list_items(self, list_id: str) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM todo_items WHERE list_id = ? ORDER BY position",
            (list_id,),
        )
        rows = await cur.fetchall()
        out = []
        for r in rows:
            e = _row(cur.description, r)
            e["done"] = bool(e["done"])
            out.append(e)
        return out

    async def get_item(self, item_id: str) -> dict | None:
        cur = await self._db.execute(
            "SELECT * FROM todo_items WHERE id = ?", (item_id,)
        )
        row = await cur.fetchone()
        if row is None:
            return None
        e = _row(cur.description, row)
        e["done"] = bool(e["done"])
        return e

    async def patch_item(
        self,
        item_id: str,
        *,
        text: str | object = _UNSET,
        done: bool | object = _UNSET,
        due_at: float | None | object = _UNSET,
        remind_at: float | None | object = _UNSET,
    ) -> dict:
        now = time.time()
        if text is not _UNSET:
            text = str(text).strip()
            if not text:
                raise ValueError("Item text cannot be empty or whitespace-only")
            await self._db.execute(
                "UPDATE todo_items SET text = ?, updated_at = ? WHERE id = ?",
                (text, now, item_id),
            )
        if done is not _UNSET:
            await self._db.execute(
                "UPDATE todo_items SET done = ?, updated_at = ? WHERE id = ?",
                (1 if done else 0, now, item_id),
            )
        if due_at is not _UNSET:
            await self._db.execute(
                "UPDATE todo_items SET due_at = ?, updated_at = ? WHERE id = ?",
                (due_at, now, item_id),
            )
        if remind_at is not _UNSET:
            await self._db.execute(
                "UPDATE todo_items SET remind_at = ?, updated_at = ? WHERE id = ?",
                (remind_at, now, item_id),
            )
        await self._db.commit()
        return await self.get_item(item_id)

    async def delete_item(self, item_id: str) -> None:
        await self._db.execute("DELETE FROM todo_items WHERE id = ?", (item_id,))
        await self._db.commit()

    async def reorder_items(self, list_id: str, items: list[dict]) -> None:
        """Batch-update positions atomically within a transaction.

        Each item: {id: str, position: int}. All items must belong to
        *list_id* and positions must be unique — invalid payloads raise
        ValueError before any writes.
        """
        # Validate that all items are present in the list
        cur = await self._db.execute(
            "SELECT id FROM todo_items WHERE list_id = ?", (list_id,)
        )
        existing_ids = {row[0] for row in await cur.fetchall()}
        payload_ids = {item["id"] for item in items}
        if payload_ids != existing_ids:
            raise ValueError(
                "Reorder payload must include exactly every item in the list"
            )

        # Validate unique, contiguous positions
        positions = [item["position"] for item in items]
        if len(set(positions)) != len(positions):
            raise ValueError("Positions must be unique")
        sorted_positions = sorted(positions)
        if sorted_positions != list(range(len(items))):
            raise ValueError(
                "Positions must be contiguous starting from 0"
            )

        now = time.time()
        await self._db.execute("BEGIN")
        try:
            for item in items:
                await self._db.execute(
                    "UPDATE todo_items SET position = ?, updated_at = ?"
                    " WHERE id = ? AND list_id = ?",
                    (item["position"], now, item["id"], list_id),
                )
            await self._db.execute(
                "UPDATE todo_lists SET updated_at = ? WHERE id = ?",
                (now, list_id),
            )
            await self._db.commit()
        except Exception:
            await self._db.execute("ROLLBACK")
            raise
