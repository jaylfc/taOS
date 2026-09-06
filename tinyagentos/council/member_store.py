from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path

from tinyagentos.base_store import BaseStore

logger = logging.getLogger(__name__)

MEMBER_STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS council_members (
    id              TEXT PRIMARY KEY,
    canonical_id    TEXT NOT NULL UNIQUE,
    model_id        TEXT NOT NULL,
    provider        TEXT NOT NULL,
    roles           TEXT NOT NULL,
    autonomy        TEXT NOT NULL,
    status          TEXT NOT NULL,
    added_at        TEXT NOT NULL
);
"""


class MemberStore(BaseStore):
    SCHEMA = MEMBER_STORE_SCHEMA

    async def add_member(
        self,
        canonical_id: str,
        model_id: str,
        provider: str,
        roles: list[dict],
        autonomy: dict,
        status: str = "active",
    ) -> dict:
        member_id = uuid.uuid4().hex
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        await self._db.execute(
            """
            INSERT INTO council_members (id, canonical_id, model_id, provider, roles, autonomy, status, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                member_id,
                canonical_id,
                model_id,
                provider,
                json.dumps(roles),
                json.dumps(autonomy),
                status,
                now,
            ),
        )
        await self._db.commit()
        return {
            "id": member_id,
            "canonical_id": canonical_id,
            "model_id": model_id,
            "provider": provider,
            "roles": roles,
            "autonomy": autonomy,
            "status": status,
            "added_at": now,
        }

    async def list_members(self) -> list[dict]:
        async with self._db.execute(
            "SELECT id, canonical_id, model_id, provider, roles, autonomy, status, added_at FROM council_members ORDER BY added_at"
        ) as cursor:
            rows = await cursor.fetchall()
        return [
            {
                "id": r[0],
                "canonical_id": r[1],
                "model_id": r[2],
                "provider": r[3],
                "roles": json.loads(r[4]),
                "autonomy": json.loads(r[5]),
                "status": r[6],
                "added_at": r[7],
            }
            for r in rows
        ]

    async def get_member(self, member_id: str) -> dict | None:
        async with self._db.execute(
            "SELECT id, canonical_id, model_id, provider, roles, autonomy, status, added_at FROM council_members WHERE id = ?",
            (member_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "canonical_id": row[1],
            "model_id": row[2],
            "provider": row[3],
            "roles": json.loads(row[4]),
            "autonomy": json.loads(row[5]),
            "status": row[6],
            "added_at": row[7],
        }
