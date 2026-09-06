from __future__ import annotations

import secrets
import time
from pathlib import Path

from tinyagentos.base_store import BaseStore

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

VALID_KINDS = frozenset({"write", "calc", "db", "slides"})

OFFICE_DOCS_SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


def _new_doc_id() -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(8))
    return f"doc-{suffix}"


class OfficeDocStore(BaseStore):
    SCHEMA = OFFICE_DOCS_SCHEMA

    def __init__(self, db_path: Path):
        super().__init__(db_path)

    async def create(self, kind: str, title: str, content: str) -> dict:
        if kind not in VALID_KINDS:
            raise ValueError(f"kind must be one of {', '.join(sorted(VALID_KINDS))}")
        for _ in range(8):
            doc_id = _new_doc_id()
            async with self._db.execute(
                "SELECT 1 FROM documents WHERE id = ?", (doc_id,)
            ) as cur:
                if await cur.fetchone() is None:
                    break
        else:
            raise RuntimeError("could not allocate document id")

        now = int(time.time())
        row = {
            "id": doc_id,
            "kind": kind,
            "title": title,
            "content": content,
            "created_at": now,
            "updated_at": now,
        }
        await self._db.execute(
            """INSERT INTO documents (id, kind, title, content, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (row["id"], row["kind"], row["title"], row["content"], row["created_at"], row["updated_at"]),
        )
        await self._db.commit()
        return row

    async def list(self) -> list[dict]:
        async with self._db.execute(
            "SELECT id, kind, title, created_at, updated_at FROM documents ORDER BY updated_at DESC"
        ) as cur:
            rows = await cur.fetchall()
        return [
            {"id": r[0], "kind": r[1], "title": r[2], "created_at": r[3], "updated_at": r[4]}
            for r in rows
        ]

    async def get(self, doc_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT id, kind, title, content, created_at, updated_at FROM documents WHERE id = ?",
            (doc_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "kind": row[1],
            "title": row[2],
            "content": row[3],
            "created_at": row[4],
            "updated_at": row[5],
        }

    async def update(
        self, doc_id: str, title: str, content: str, kind: str | None = None
    ) -> dict | None:
        now = int(time.time())
        if kind is not None:
            if kind not in VALID_KINDS:
                raise ValueError(f"kind must be one of {', '.join(sorted(VALID_KINDS))}")
            await self._db.execute(
                "UPDATE documents SET kind = ?, title = ?, content = ?, updated_at = ? WHERE id = ?",
                (kind, title, content, now, doc_id),
            )
        else:
            await self._db.execute(
                "UPDATE documents SET title = ?, content = ?, updated_at = ? WHERE id = ?",
                (title, content, now, doc_id),
            )
        await self._db.commit()
        return await self.get(doc_id)

    async def delete(self, doc_id: str) -> bool:
        async with self._db.execute(
            "SELECT 1 FROM documents WHERE id = ?", (doc_id,)
        ) as cur:
            exists = await cur.fetchone() is not None
        if not exists:
            return False
        await self._db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
        await self._db.commit()
        return True
