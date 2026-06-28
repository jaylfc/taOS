"""SQLite-backed store for shared notes and lists.

A document is a note (``kind="note"``) or a list (``kind="list"``). Both are
ordered collections of entries; a list entry additionally tracks a ``done``
flag. A document has members: the owner (a user) plus any number of agents,
each agent share carrying a ``standing_instruction`` that says what the agent
should do when a new entry appears. The reaction itself is dispatched by the
route layer; this store only records the data and reports which agents to
notify after an entry is added.

Append-only-friendly: archiving sets ``archived_at`` rather than deleting, so
nothing is truly lost (#103).
"""

from __future__ import annotations

import json
import time

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

DOC_KINDS = ("note", "list")

SHARED_DOCS_SCHEMA = """
CREATE TABLE IF NOT EXISTS shared_docs (
    id            TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    kind          TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    created_at    REAL NOT NULL,
    updated_at    REAL NOT NULL,
    archived_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_shared_docs_owner ON shared_docs(owner_user_id, archived_at);

CREATE TABLE IF NOT EXISTS shared_doc_entries (
    id         TEXT PRIMARY KEY,
    doc_id     TEXT NOT NULL,
    text       TEXT NOT NULL DEFAULT '',
    done       INTEGER NOT NULL DEFAULT 0,
    author     TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_shared_doc_entries_doc ON shared_doc_entries(doc_id, created_at);

CREATE TABLE IF NOT EXISTS shared_doc_members (
    doc_id               TEXT NOT NULL,
    member_type          TEXT NOT NULL,   -- 'user' | 'agent'
    member_id            TEXT NOT NULL,
    standing_instruction TEXT NOT NULL DEFAULT '',
    created_at           REAL NOT NULL,
    PRIMARY KEY (doc_id, member_type, member_id)
);
CREATE INDEX IF NOT EXISTS idx_shared_doc_members_doc ON shared_doc_members(doc_id);
CREATE INDEX IF NOT EXISTS idx_shared_doc_members_agent ON shared_doc_members(member_type, member_id);
"""


def _row(cursor_desc, row) -> dict:
    return dict(zip([c[0] for c in cursor_desc], row))


class SharedDocsStore(BaseStore):
    SCHEMA = SHARED_DOCS_SCHEMA

    # ------------------------------------------------------------------ docs
    async def create_doc(self, owner_user_id: str, kind: str, title: str = "") -> dict:
        if kind not in DOC_KINDS:
            raise ValueError(f"invalid kind {kind!r}; expected one of {DOC_KINDS}")
        doc_id = new_id("doc")
        now = time.time()
        await self._db.execute(
            "INSERT INTO shared_docs (id, owner_user_id, kind, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (doc_id, owner_user_id, kind, title, now, now),
        )
        # The owner is always a member.
        await self._db.execute(
            "INSERT INTO shared_doc_members (doc_id, member_type, member_id, created_at) "
            "VALUES (?, 'user', ?, ?)",
            (doc_id, owner_user_id, now),
        )
        await self._db.commit()
        return await self.get_doc(doc_id)

    async def list_docs(self, user_id: str, *, include_archived: bool = False) -> list[dict]:
        """Docs the user owns or is shared on, newest-updated first."""
        q = (
            "SELECT DISTINCT d.* FROM shared_docs d "
            "LEFT JOIN shared_doc_members m ON m.doc_id = d.id "
            "WHERE (d.owner_user_id = ? OR (m.member_type = 'user' AND m.member_id = ?)) "
        )
        if not include_archived:
            q += "AND d.archived_at IS NULL "
        q += "ORDER BY d.updated_at DESC"
        cur = await self._db.execute(q, (user_id, user_id))
        rows = await cur.fetchall()
        return [_row(cur.description, r) for r in rows]

    async def get_doc(self, doc_id: str) -> dict | None:
        cur = await self._db.execute("SELECT * FROM shared_docs WHERE id = ?", (doc_id,))
        row = await cur.fetchone()
        if row is None:
            return None
        doc = _row(cur.description, row)
        doc["entries"] = await self.list_entries(doc_id)
        doc["members"] = await self.list_members(doc_id)
        return doc

    async def set_title(self, doc_id: str, title: str) -> None:
        await self._db.execute(
            "UPDATE shared_docs SET title = ?, updated_at = ? WHERE id = ?",
            (title, time.time(), doc_id),
        )
        await self._db.commit()

    async def archive_doc(self, doc_id: str) -> None:
        await self._db.execute(
            "UPDATE shared_docs SET archived_at = ?, updated_at = ? WHERE id = ?",
            (time.time(), time.time(), doc_id),
        )
        await self._db.commit()

    # --------------------------------------------------------------- entries
    async def add_entry(self, doc_id: str, text: str, author: str = "") -> dict:
        entry_id = new_id("ent")
        now = time.time()
        await self._db.execute(
            "INSERT INTO shared_doc_entries (id, doc_id, text, author, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (entry_id, doc_id, text, author, now),
        )
        await self._db.execute(
            "UPDATE shared_docs SET updated_at = ? WHERE id = ?", (now, doc_id)
        )
        await self._db.commit()
        cur = await self._db.execute("SELECT * FROM shared_doc_entries WHERE id = ?", (entry_id,))
        return _row(cur.description, await cur.fetchone())

    async def list_entries(self, doc_id: str) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM shared_doc_entries WHERE doc_id = ? ORDER BY created_at", (doc_id,)
        )
        rows = await cur.fetchall()
        out = []
        for r in rows:
            e = _row(cur.description, r)
            e["done"] = bool(e["done"])
            out.append(e)
        return out

    async def set_entry_done(self, entry_id: str, done: bool) -> None:
        await self._db.execute(
            "UPDATE shared_doc_entries SET done = ? WHERE id = ?", (1 if done else 0, entry_id)
        )
        await self._db.commit()

    async def delete_entry(self, entry_id: str) -> None:
        await self._db.execute("DELETE FROM shared_doc_entries WHERE id = ?", (entry_id,))
        await self._db.commit()

    # --------------------------------------------------------------- members
    async def add_member(
        self,
        doc_id: str,
        member_type: str,
        member_id: str,
        standing_instruction: str = "",
    ) -> None:
        if member_type not in ("user", "agent"):
            raise ValueError(f"invalid member_type {member_type!r}")
        await self._db.execute(
            "INSERT OR REPLACE INTO shared_doc_members "
            "(doc_id, member_type, member_id, standing_instruction, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (doc_id, member_type, member_id, standing_instruction, time.time()),
        )
        await self._db.commit()

    async def remove_member(self, doc_id: str, member_type: str, member_id: str) -> None:
        await self._db.execute(
            "DELETE FROM shared_doc_members WHERE doc_id = ? AND member_type = ? AND member_id = ?",
            (doc_id, member_type, member_id),
        )
        await self._db.commit()

    async def list_members(self, doc_id: str) -> list[dict]:
        cur = await self._db.execute(
            "SELECT * FROM shared_doc_members WHERE doc_id = ? ORDER BY created_at", (doc_id,)
        )
        rows = await cur.fetchall()
        return [_row(cur.description, r) for r in rows]

    async def agent_members(self, doc_id: str) -> list[dict]:
        """Agent shares for a doc -- the targets to notify on a new entry,
        each with its standing_instruction."""
        cur = await self._db.execute(
            "SELECT member_id, standing_instruction FROM shared_doc_members "
            "WHERE doc_id = ? AND member_type = 'agent'",
            (doc_id,),
        )
        rows = await cur.fetchall()
        return [{"agent": r[0], "standing_instruction": r[1]} for r in rows]
