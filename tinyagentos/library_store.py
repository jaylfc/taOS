"""Persistent store for Library items, artifacts, and processing jobs.

The Library is the universal ingestion surface for taOS — files, URLs, media
dropped into the Library get processed and their text artifacts indexed into
taosmd collections for agent access.

Schema mirrors the design doc (docs/design/library-app.md):
  - items: one row per ingested thing (file, URL, paste)
  - artifacts: derived outputs (metadata, transcript, thumbnail, OCR text)
  - jobs: async processing stages with retry support
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import aiosqlite

from tinyagentos.base_store import BaseStore

LIBRARY_SCHEMA = """
CREATE TABLE IF NOT EXISTS library_items (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    storage_path TEXT NOT NULL DEFAULT '',
    bytes INTEGER NOT NULL DEFAULT 0,
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_li_kind ON library_items(kind);
CREATE INDEX IF NOT EXISTS idx_li_status ON library_items(status);
CREATE INDEX IF NOT EXISTS idx_li_created ON library_items(created_at DESC);

CREATE TABLE IF NOT EXISTS library_artifacts (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
    kind TEXT NOT NULL,
    path TEXT NOT NULL DEFAULT '',
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_la_item ON library_artifacts(item_id);

CREATE TABLE IF NOT EXISTS library_jobs (
    id TEXT PRIMARY KEY,
    item_id TEXT NOT NULL REFERENCES library_items(id) ON DELETE CASCADE,
    stage TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lj_item ON library_jobs(item_id);
CREATE INDEX IF NOT EXISTS idx_lj_state ON library_jobs(state);

CREATE TABLE IF NOT EXISTS library_rules (
    id TEXT PRIMARY KEY,
    source_pattern TEXT NOT NULL,
    quality TEXT NOT NULL DEFAULT '720',
    auto_download INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_lr_pattern ON library_rules(source_pattern);
"""

_VALID_STATUSES = frozenset({"pending", "processing", "ready", "error"})


class LibraryStore(BaseStore):
    SCHEMA = LIBRARY_SCHEMA

    async def _post_init(self) -> None:
        """Enable foreign key enforcement and apply schema migrations."""
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.commit()

        # P3 migration: add quality, auto_download, downloaded_at columns
        # if they don't exist yet (existing databases from P1/P2).
        cols = await self._db.execute_fetchall("PRAGMA table_info(library_items)")
        col_names = {row[1] for row in cols}
        for col, col_def in [
            ("quality", "TEXT NOT NULL DEFAULT ''"),
            ("auto_download", "INTEGER NOT NULL DEFAULT 0"),
            ("downloaded_at", "REAL"),
            ("download_path", "TEXT NOT NULL DEFAULT ''"),
            ("download_bytes", "INTEGER NOT NULL DEFAULT 0"),
        ]:
            if col not in col_names:
                await self._db.execute(
                    f"ALTER TABLE library_items ADD COLUMN {col} {col_def}"
                )
        if col_names:
            await self._db.commit()

    # -- items ------------------------------------------------------------

    async def create_item(
        self,
        kind: str,
        source_url: str = "",
        title: str = "",
        storage_path: str = "",
        size_bytes: int = 0,
        meta: dict | None = None,
    ) -> str:
        """Create a new library item. Returns the item id."""
        item_id = uuid.uuid4().hex
        now = time.time()
        await self._db.execute(
            """INSERT INTO library_items
               (id, kind, source_url, title, status, storage_path, bytes,
                meta_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)""",
            (
                item_id,
                kind,
                source_url,
                title,
                storage_path,
                size_bytes,
                json.dumps(meta or {}),
                now,
                now,
            ),
        )
        await self._db.commit()
        return item_id

    async def get_item(self, item_id: str) -> dict | None:
        self._db.row_factory = aiosqlite.Row
        async with self._db.execute(
            "SELECT * FROM library_items WHERE id = ?", (item_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def list_items(
        self, kind: str | None = None, status: str | None = None,
        limit: int = 50, offset: int = 0,
    ) -> list[dict]:
        self._db.row_factory = aiosqlite.Row
        where: list[str] = []
        params: list = []
        if kind:
            where.append("kind = ?")
            params.append(kind)
        if status:
            where.append("status = ?")
            params.append(status)

        sql = "SELECT * FROM library_items"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def update_item(self, item_id: str, **kwargs) -> None:
        allowed = {"kind", "source_url", "title", "status", "storage_path",
                    "bytes", "meta_json", "updated_at",
                    "quality", "auto_download", "download_path",
                    "download_bytes", "downloaded_at"}
        fields = [(k, v) for k, v in kwargs.items() if k in allowed]
        if not fields:
            return
        if "updated_at" not in kwargs:
            fields.append(("updated_at", time.time()))
        if "meta_json" in kwargs and isinstance(kwargs["meta_json"], dict):
            # find and replace the meta_json tuple
            for i, (k, _v) in enumerate(fields):
                if k == "meta_json":
                    fields[i] = ("meta_json", json.dumps(kwargs["meta_json"]))
                    break

        set_clause = ", ".join(f"{k} = ?" for k, _ in fields)
        values = [v for _, v in fields]
        values.append(item_id)
        await self._db.execute(
            f"UPDATE library_items SET {set_clause} WHERE id = ?", values
        )
        await self._db.commit()

    async def delete_item(self, item_id: str) -> None:
        """Delete an item and cascade its artifacts and jobs."""
        await self._db.execute("DELETE FROM library_items WHERE id = ?", (item_id,))
        await self._db.commit()

    async def update_item_status(self, item_id: str, status: str) -> None:
        if status not in _VALID_STATUSES:
            raise ValueError(
                f"Invalid status {status!r}; must be one of {sorted(_VALID_STATUSES)}"
            )
        await self.update_item(item_id, status=status)

    async def try_update_item_status(
        self, item_id: str, new_status: str, *,
        if_not_in: tuple[str, ...] = ("pending", "processing"),
    ) -> bool:
        """Atomically set status to *new_status* when current status is NOT in *if_not_in*.

        Returns True when a row was updated, False otherwise.
        Used by reprocess to avoid TOCTOU races — two concurrent reprocess
        requests cannot both pass a read-then-write guard.
        """
        if new_status not in _VALID_STATUSES:
            raise ValueError(
                f"Invalid status {new_status!r}; must be one of {sorted(_VALID_STATUSES)}"
            )
        placeholders = ",".join("?" * len(if_not_in))
        params = [new_status, time.time(), item_id, *if_not_in]
        cursor = await self._db.execute(
            f"UPDATE library_items SET status = ?, updated_at = ? "
            f"WHERE id = ? AND status NOT IN ({placeholders})",
            params,
        )
        await self._db.commit()
        return cursor.rowcount > 0

    # -- artifacts --------------------------------------------------------

    async def add_artifact(
        self, item_id: str, kind: str, path: str = "",
        meta: dict | None = None,
    ) -> str:
        artifact_id = uuid.uuid4().hex[:16]
        now = time.time()
        await self._db.execute(
            """INSERT INTO library_artifacts (id, item_id, kind, path, meta_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (artifact_id, item_id, kind, path, json.dumps(meta or {}), now),
        )
        await self._db.commit()
        return artifact_id

    async def get_artifacts(self, item_id: str) -> list[dict]:
        self._db.row_factory = aiosqlite.Row
        async with self._db.execute(
            "SELECT * FROM library_artifacts WHERE item_id = ? ORDER BY created_at",
            (item_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def delete_artifact(self, artifact_id: str) -> None:
        await self._db.execute(
            "DELETE FROM library_artifacts WHERE id = ?", (artifact_id,)
        )
        await self._db.commit()

    # -- jobs -------------------------------------------------------------

    async def create_job(self, item_id: str, stage: str) -> str:
        job_id = uuid.uuid4().hex[:16]
        now = time.time()
        await self._db.execute(
            """INSERT INTO library_jobs (id, item_id, stage, state, created_at, updated_at)
               VALUES (?, ?, ?, 'queued', ?, ?)""",
            (job_id, item_id, stage, now, now),
        )
        await self._db.commit()
        return job_id

    async def get_job(self, job_id: str) -> dict | None:
        self._db.row_factory = aiosqlite.Row
        async with self._db.execute(
            "SELECT * FROM library_jobs WHERE id = ?", (job_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_item_jobs(self, item_id: str) -> list[dict]:
        self._db.row_factory = aiosqlite.Row
        async with self._db.execute(
            "SELECT * FROM library_jobs WHERE item_id = ? ORDER BY created_at",
            (item_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def list_jobs(
        self, state: str | None = None, limit: int = 100,
    ) -> list[dict]:
        self._db.row_factory = aiosqlite.Row
        where: list[str] = []
        params: list = []
        if state:
            where.append("state = ?")
            params.append(state)

        sql = "SELECT * FROM library_jobs"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def update_job(self, job_id: str, **kwargs) -> None:
        allowed = {"state", "error", "updated_at"}
        fields = [(k, v) for k, v in kwargs.items() if k in allowed]
        if not fields:
            return
        if "updated_at" not in kwargs:
            fields.append(("updated_at", time.time()))

        set_clause = ", ".join(f"{k} = ?" for k, _ in fields)
        values = [v for _, v in fields]
        values.append(job_id)
        await self._db.execute(
            f"UPDATE library_jobs SET {set_clause} WHERE id = ?", values
        )
        await self._db.commit()

    # -- source rules -----------------------------------------------------

    async def create_rule(
        self,
        source_pattern: str,
        quality: str = "720",
        auto_download: bool = False,
        enabled: bool = True,
    ) -> str:
        rule_id = uuid.uuid4().hex[:16]
        now = time.time()
        await self._db.execute(
            """INSERT INTO library_rules
               (id, source_pattern, quality, auto_download, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (rule_id, source_pattern, quality, int(auto_download), int(enabled), now),
        )
        await self._db.commit()
        return rule_id

    async def list_rules(self) -> list[dict]:
        self._db.row_factory = aiosqlite.Row
        async with self._db.execute(
            "SELECT * FROM library_rules ORDER BY created_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_rule(self, rule_id: str) -> dict | None:
        self._db.row_factory = aiosqlite.Row
        async with self._db.execute(
            "SELECT * FROM library_rules WHERE id = ?", (rule_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def delete_rule(self, rule_id: str) -> None:
        await self._db.execute(
            "DELETE FROM library_rules WHERE id = ?", (rule_id,)
        )
        await self._db.commit()

    async def match_rules(self, source_url: str) -> list[dict]:
        """Return all enabled rules whose source_pattern matches source_url."""
        self._db.row_factory = aiosqlite.Row
        async with self._db.execute(
            "SELECT * FROM library_rules WHERE enabled = 1 ORDER BY created_at"
        ) as cursor:
            rows = await cursor.fetchall()
        rules = [dict(r) for r in rows]
        import fnmatch
        return [
            r for r in rules
            if fnmatch.fnmatch(source_url.lower(), r["source_pattern"].lower())
        ]

    # -- storage accounting -----------------------------------------------

    async def get_storage_summary(self) -> dict:
        """Return total bytes, item count, and per-kind breakdown."""
        self._db.row_factory = aiosqlite.Row
        rows = await self._db.execute_fetchall(
            "SELECT kind, COUNT(*) as count, "
            "COALESCE(SUM(bytes), 0) as total_bytes "
            "FROM library_items GROUP BY kind"
        )
        by_kind = {row["kind"]: {"count": row["count"], "bytes": row["total_bytes"]} for row in rows}

        total = await self._db.execute_fetchall(
            "SELECT COUNT(*) as total_count, COALESCE(SUM(bytes), 0) as total_bytes "
            "FROM library_items"
        )
        if total:
            return {
                "total_count": total[0]["total_count"],
                "total_bytes": total[0]["total_bytes"],
                "by_kind": by_kind,
            }
        return {"total_count": 0, "total_bytes": 0, "by_kind": {}}
