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

Every text change to an entry is recorded as an immutable revision row. The
revision log stores a diff per edit plus a full-text snapshot every
CHECKPOINT_EVERY revisions so that rewinding to any past state requires at
most one checkpoint load plus a short diff replay.
"""

from __future__ import annotations

import time

from diff_match_patch import diff_match_patch

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

DOC_KINDS = ("note", "list")

CHECKPOINT_EVERY = 20

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

CREATE TABLE IF NOT EXISTS shared_doc_entry_revisions (
    id          TEXT PRIMARY KEY,
    entry_id    TEXT NOT NULL,
    rev_index   INTEGER NOT NULL,
    editor_id   TEXT NOT NULL DEFAULT '',
    editor_type TEXT NOT NULL DEFAULT 'user',
    op          TEXT NOT NULL,
    diff        TEXT,
    snapshot    TEXT,
    created_at  REAL NOT NULL,
    UNIQUE (entry_id, rev_index)
);
CREATE INDEX IF NOT EXISTS idx_entry_revisions_entry ON shared_doc_entry_revisions(entry_id, rev_index);
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
        rev_id = new_id("rev")
        await self._db.execute(
            "INSERT INTO shared_doc_entry_revisions "
            "(id, entry_id, rev_index, editor_id, editor_type, op, diff, snapshot, created_at) "
            "VALUES (?, ?, 0, ?, 'user', 'create', NULL, ?, ?)",
            (rev_id, entry_id, author, text, now),
        )
        await self._db.commit()
        cur = await self._db.execute("SELECT * FROM shared_doc_entries WHERE id = ?", (entry_id,))
        return _row(cur.description, await cur.fetchone())

    async def edit_entry(
        self,
        entry_id: str,
        new_text: str,
        editor_id: str = "",
        editor_type: str = "user",
    ) -> dict:
        cur = await self._db.execute(
            "SELECT text FROM shared_doc_entries WHERE id = ?", (entry_id,)
        )
        row = await cur.fetchone()
        if row is None:
            raise KeyError(f"entry not found: {entry_id}")
        old_text = row[0]

        dmp = diff_match_patch()
        patches = dmp.patch_make(old_text, new_text)
        diff_text = dmp.patch_toText(patches)

        cur = await self._db.execute(
            "SELECT MAX(rev_index) FROM shared_doc_entry_revisions WHERE entry_id = ?",
            (entry_id,),
        )
        row_max = await cur.fetchone()
        next_index = (row_max[0] or 0) + 1

        snapshot = new_text if next_index % CHECKPOINT_EVERY == 0 else None

        rev_id = new_id("rev")
        now = time.time()
        await self._db.execute(
            "INSERT INTO shared_doc_entry_revisions "
            "(id, entry_id, rev_index, editor_id, editor_type, op, diff, snapshot, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'edit', ?, ?, ?)",
            (rev_id, entry_id, next_index, editor_id, editor_type, diff_text, snapshot, now),
        )
        await self._db.execute(
            "UPDATE shared_doc_entries SET text = ? WHERE id = ?", (new_text, entry_id)
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

    # ------------------------------------------------------------ revision reads

    async def list_revisions(self, entry_id: str) -> list[dict]:
        """Return all revisions for an entry, ascending by rev_index.

        Each dict contains: rev_index, editor_id, editor_type, op, created_at.
        The diff and snapshot columns are intentionally omitted here; use
        revision_diff / entry_text_at for those.
        """
        cur = await self._db.execute(
            "SELECT rev_index, editor_id, editor_type, op, created_at "
            "FROM shared_doc_entry_revisions WHERE entry_id = ? ORDER BY rev_index",
            (entry_id,),
        )
        rows = await cur.fetchall()
        return [dict(zip([c[0] for c in cur.description], r)) for r in rows]

    async def revision_diff(self, entry_id: str, rev_index: int) -> str | None:
        """Return the stored diff text for the given revision (None for create rows)."""
        cur = await self._db.execute(
            "SELECT diff FROM shared_doc_entry_revisions WHERE entry_id = ? AND rev_index = ?",
            (entry_id, rev_index),
        )
        row = await cur.fetchone()
        if row is None:
            raise KeyError(f"revision not found: entry={entry_id} rev={rev_index}")
        return row[0]

    async def entry_text_at(self, entry_id: str, rev_index: int) -> str:
        """Reconstruct the entry text as it was after revision rev_index.

        Strategy: find the highest revision <= rev_index whose snapshot is set,
        then apply each subsequent revision's diff up to rev_index. Raises
        KeyError if entry or revision is missing, ValueError if a diff fails.
        """
        cur = await self._db.execute(
            "SELECT rev_index, snapshot, diff FROM shared_doc_entry_revisions "
            "WHERE entry_id = ? AND rev_index <= ? ORDER BY rev_index",
            (entry_id, rev_index),
        )
        rows = await cur.fetchall()
        if not rows:
            raise KeyError(f"no revisions found: entry={entry_id} rev<={rev_index}")

        # Find the last checkpoint at or before rev_index.
        base_text = None
        base_rev = -1
        for r in rows:
            r_idx, r_snapshot, _ = r
            if r_snapshot is not None:
                base_text = r_snapshot
                base_rev = r_idx

        if base_text is None:
            raise ValueError(
                f"no snapshot/checkpoint found for entry={entry_id} up to rev={rev_index}; "
                "revision log may be corrupt"
            )

        if base_rev == rev_index:
            return base_text

        # Replay diffs from base_rev+1 up to rev_index.
        dmp = diff_match_patch()
        text = base_text
        for r in rows:
            r_idx, _, r_diff = r
            if r_idx <= base_rev:
                continue
            if r_diff is None:
                continue
            patches = dmp.patch_fromText(r_diff)
            text, results = dmp.patch_apply(patches, text)
            if not all(results):
                raise ValueError(
                    f"diff application failed at rev={r_idx} for entry={entry_id}; "
                    "revision log may be corrupt"
                )
        return text

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
