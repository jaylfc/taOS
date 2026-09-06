"""SQLite-backed store for coding sessions.

A coding session records an external coding CLI (Claude Code / Codex / opencode)
that the user launches on the OS to work on a repo. This store is the data layer
only; the launcher (tmux, LXC, cloning) lives in a later slice.

Records are append-only: archive sets status="archived" but never deletes rows.
"""

from __future__ import annotations

import json
import re
import time

from tinyagentos.base_store import BaseStore
from tinyagentos.projects.ids import new_id

VALID_CLIS = ("claude", "codex", "opencode")
VALID_LAUNCH_TARGETS = ("host-folder", "host-clone", "controller-lxc", "worker-lxc")
VALID_STATUSES = ("starting", "running", "waiting_input", "stopped", "archived")

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("-", text.lower().strip()).strip("-") or "session"


SCHEMA = """
CREATE TABLE IF NOT EXISTS coding_sessions (
    id             TEXT PRIMARY KEY,
    alias          TEXT NOT NULL,
    cli            TEXT NOT NULL,
    launch_target  TEXT NOT NULL,
    worker         TEXT,
    workdir        TEXT NOT NULL,
    repo_source    TEXT NOT NULL DEFAULT '{}',
    tmux_session   TEXT,
    status         TEXT NOT NULL DEFAULT 'starting',
    project_id     TEXT,
    created_by     TEXT NOT NULL,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    archived_at    REAL
);
CREATE INDEX IF NOT EXISTS idx_cs_created_by ON coding_sessions(created_by, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cs_status ON coding_sessions(status, created_at DESC);

CREATE TABLE IF NOT EXISTS coding_session_transcript (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    captured_at REAL NOT NULL,
    text        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cst_session ON coding_session_transcript(session_id, id);
"""

_JSON_FIELDS = ("repo_source",)


def _row_to_session(row, description) -> dict:
    d = dict(zip([c[0] for c in description], row))
    for f in _JSON_FIELDS:
        if d.get(f) is not None:
            d[f] = json.loads(d[f])
    return d


class CodingSessionStore(BaseStore):
    SCHEMA = SCHEMA

    async def create_session(
        self,
        *,
        cli: str,
        launch_target: str,
        workdir: str,
        repo_source: dict,
        created_by: str,
        alias: str = "",
        worker: str | None = None,
        project_id: str | None = None,
    ) -> dict:
        sid = new_id("cs")
        now = time.time()
        resolved_alias = alias.strip() if alias and alias.strip() else _slugify(workdir.split("/")[-1] or workdir)
        await self._db.execute(
            """INSERT INTO coding_sessions
               (id, alias, cli, launch_target, worker, workdir, repo_source,
                tmux_session, status, project_id, created_by, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'starting', ?, ?, ?, ?)""",
            (
                sid, resolved_alias, cli, launch_target, worker, workdir,
                json.dumps(repo_source), project_id, created_by, now, now,
            ),
        )
        await self._db.commit()
        return await self.get_session(sid)

    async def get_session(self, session_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT * FROM coding_sessions WHERE id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
            if row is None:
                return None
            return _row_to_session(row, cur.description)

    async def list_sessions(
        self,
        created_by: str,
        *,
        include_archived: bool = False,
    ) -> list[dict]:
        if include_archived:
            query = (
                "SELECT * FROM coding_sessions WHERE created_by = ? ORDER BY created_at DESC",
                (created_by,),
            )
        else:
            query = (
                "SELECT * FROM coding_sessions WHERE created_by = ? AND status != 'archived' ORDER BY created_at DESC",
                (created_by,),
            )
        async with self._db.execute(*query) as cur:
            rows = await cur.fetchall()
            desc = cur.description
        return [_row_to_session(r, desc) for r in rows]

    async def set_status(self, session_id: str, status: str) -> dict | None:
        now = time.time()
        await self._db.execute(
            "UPDATE coding_sessions SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, session_id),
        )
        await self._db.commit()
        return await self.get_session(session_id)

    async def set_tmux_session(self, session_id: str, name: str) -> dict | None:
        now = time.time()
        await self._db.execute(
            "UPDATE coding_sessions SET tmux_session = ?, updated_at = ? WHERE id = ?",
            (name, now, session_id),
        )
        await self._db.commit()
        return await self.get_session(session_id)

    async def set_alias(self, session_id: str, alias: str) -> dict | None:
        now = time.time()
        await self._db.execute(
            "UPDATE coding_sessions SET alias = ?, updated_at = ? WHERE id = ?",
            (alias, now, session_id),
        )
        await self._db.commit()
        return await self.get_session(session_id)

    async def archive_session(self, session_id: str) -> dict | None:
        now = time.time()
        await self._db.execute(
            "UPDATE coding_sessions SET status = 'archived', archived_at = ?, updated_at = ? WHERE id = ?",
            (now, now, session_id),
        )
        await self._db.commit()
        return await self.get_session(session_id)

    # ----------------------------------------------------------- transcript
    async def append_transcript(self, session_id: str, text: str) -> None:
        """Append a captured chunk of terminal output (append-only, never edited)."""
        if not text:
            return
        await self._db.execute(
            "INSERT INTO coding_session_transcript (session_id, captured_at, text) "
            "VALUES (?, ?, ?)",
            (session_id, time.time(), text),
        )
        await self._db.commit()

    async def get_transcript(self, session_id: str, *, limit: int = 500) -> list[dict]:
        """Return transcript chunks oldest-first (most recent ``limit`` entries)."""
        cur = await self._db.execute(
            "SELECT id, captured_at, text FROM ("
            "  SELECT id, captured_at, text FROM coding_session_transcript "
            "  WHERE session_id = ? ORDER BY id DESC LIMIT ?"
            ") ORDER BY id ASC",
            (session_id, limit),
        )
        rows = await cur.fetchall()
        return [{"id": r[0], "captured_at": r[1], "text": r[2]} for r in rows]
