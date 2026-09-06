from __future__ import annotations

import secrets
import time
from pathlib import Path
from uuid import uuid4

from tinyagentos.base_store import BaseStore

_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"

STORE_SUBMISSIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS store_submissions (
    id TEXT PRIMARY KEY,
    artifact_id TEXT NOT NULL,
    artifact_kind TEXT NOT NULL,
    owner_id TEXT NOT NULL,
    title TEXT NOT NULL,
    publish_mode TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft',
    gitaos_ref TEXT,
    reject_reason TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
"""


def _new_id() -> str:
    suffix = "".join(secrets.choice(_ALPHABET) for _ in range(6))
    return f"ss-{suffix}"


_VALID_STATUSES = {"draft", "pending_verification", "published", "rejected"}
_VALID_KINDS = {"app", "game", "project", "workflow", "studio"}
_VALID_PUBLISH_MODES = {"repo", "bundle"}

_TRANSITIONS = {
    "draft": {"pending_verification"},
    "pending_verification": {"published", "rejected"},
}


class StoreSubmissionStore(BaseStore):
    SCHEMA = STORE_SUBMISSIONS_SCHEMA

    async def create(
        self,
        artifact_id: str,
        artifact_kind: str,
        owner_id: str,
        title: str,
        publish_mode: str,
    ) -> dict:
        if artifact_kind not in _VALID_KINDS:
            raise ValueError(f"invalid artifact_kind: {artifact_kind}")
        if publish_mode not in _VALID_PUBLISH_MODES:
            raise ValueError(f"invalid publish_mode: {publish_mode}")
        now = int(time.time())
        for _ in range(8):
            sid = _new_id()
            async with self._db.execute(
                "SELECT 1 FROM store_submissions WHERE id = ?", (sid,)
            ) as cur:
                if await cur.fetchone() is None:
                    break
        else:
            raise RuntimeError("could not allocate store submission id")
        row = {
            "id": sid,
            "artifact_id": artifact_id,
            "artifact_kind": artifact_kind,
            "owner_id": owner_id,
            "title": title,
            "publish_mode": publish_mode,
            "status": "draft",
            "gitaos_ref": None,
            "reject_reason": None,
            "created_at": now,
            "updated_at": now,
        }
        await self._db.execute(
            """INSERT INTO store_submissions
               (id, artifact_id, artifact_kind, owner_id, title, publish_mode,
                status, gitaos_ref, reject_reason, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row["id"], row["artifact_id"], row["artifact_kind"],
                row["owner_id"], row["title"], row["publish_mode"],
                row["status"], row["gitaos_ref"], row["reject_reason"],
                row["created_at"], row["updated_at"],
            ),
        )
        await self._db.commit()
        return row

    async def submit(self, submission_id: str) -> dict:
        row = await self.get(submission_id)
        if row is None:
            raise ValueError("submission not found")
        current = row["status"]
        allowed = _TRANSITIONS.get(current, set())
        if "pending_verification" not in allowed:
            raise ValueError(
                f"cannot submit from status {current!r}; must be 'draft'"
            )
        now = int(time.time())
        await self._db.execute(
            "UPDATE store_submissions SET status = ?, updated_at = ? WHERE id = ?",
            ("pending_verification", now, submission_id),
        )
        await self._db.commit()
        return await self.get(submission_id)

    async def set_gitaos_ref(self, submission_id: str, ref: str) -> dict:
        row = await self.get(submission_id)
        if row is None:
            raise ValueError("submission not found")
        now = int(time.time())
        await self._db.execute(
            "UPDATE store_submissions SET gitaos_ref = ?, updated_at = ? WHERE id = ?",
            (ref, now, submission_id),
        )
        await self._db.commit()
        return await self.get(submission_id)

    async def approve(self, submission_id: str) -> dict:
        row = await self.get(submission_id)
        if row is None:
            raise ValueError("submission not found")
        current = row["status"]
        allowed = _TRANSITIONS.get(current, set())
        if "published" not in allowed:
            raise ValueError(
                f"cannot approve from status {current!r}; must be 'pending_verification'"
            )
        now = int(time.time())
        await self._db.execute(
            "UPDATE store_submissions SET status = ?, updated_at = ? WHERE id = ?",
            ("published", now, submission_id),
        )
        await self._db.commit()
        return await self.get(submission_id)

    async def reject(self, submission_id: str, reason: str) -> dict:
        row = await self.get(submission_id)
        if row is None:
            raise ValueError("submission not found")
        current = row["status"]
        allowed = _TRANSITIONS.get(current, set())
        if "rejected" not in allowed:
            raise ValueError(
                f"cannot reject from status {current!r}; must be 'pending_verification'"
            )
        now = int(time.time())
        await self._db.execute(
            "UPDATE store_submissions SET status = ?, reject_reason = ?, updated_at = ? WHERE id = ?",
            ("rejected", reason, now, submission_id),
        )
        await self._db.commit()
        return await self.get(submission_id)

    async def list(
        self,
        owner_id: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        sql = """SELECT id, artifact_id, artifact_kind, owner_id, title,
                        publish_mode, status, gitaos_ref, reject_reason,
                        created_at, updated_at
                 FROM store_submissions"""
        params: list = []
        clauses: list[str] = []
        if owner_id is not None:
            clauses.append("owner_id = ?")
            params.append(owner_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at ASC"
        async with self._db.execute(sql, params) as cur:
            rows = await cur.fetchall()
        return [
            {
                "id": r[0], "artifact_id": r[1], "artifact_kind": r[2],
                "owner_id": r[3], "title": r[4], "publish_mode": r[5],
                "status": r[6], "gitaos_ref": r[7], "reject_reason": r[8],
                "created_at": r[9], "updated_at": r[10],
            }
            for r in rows
        ]

    async def get(self, submission_id: str) -> dict | None:
        async with self._db.execute(
            """SELECT id, artifact_id, artifact_kind, owner_id, title,
                      publish_mode, status, gitaos_ref, reject_reason,
                      created_at, updated_at
               FROM store_submissions WHERE id = ?""",
            (submission_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "id": row[0], "artifact_id": row[1], "artifact_kind": row[2],
            "owner_id": row[3], "title": row[4], "publish_mode": row[5],
            "status": row[6], "gitaos_ref": row[7], "reject_reason": row[8],
            "created_at": row[9], "updated_at": row[10],
        }
