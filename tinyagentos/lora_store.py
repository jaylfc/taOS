"""Persistent store for LoRA Studio -- archived Civitai LoRA/LoCon/DoRA models.

Schema mirrors the LoRA Studio spec (section 3): one row per ingested model,
tracking the Civitai source, the downloaded safetensors file, preview
images, and ingest status. Files themselves live under
``models_root()/loras/<lora_slug>/`` (see tinyagentos/routes/lora_studio.py)
-- this store only tracks metadata.
"""

from __future__ import annotations

import json
import time

import aiosqlite

from tinyagentos.base_store import BaseStore

LORA_SCHEMA = """
CREATE TABLE IF NOT EXISTS loras (
    id TEXT PRIMARY KEY,
    source_url TEXT NOT NULL DEFAULT '',
    provider TEXT NOT NULL DEFAULT 'civitai',
    civitai_model_id INTEGER,
    civitai_version_id INTEGER,
    name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    creator TEXT NOT NULL DEFAULT '',
    base_model TEXT NOT NULL DEFAULT '',
    trigger_words TEXT NOT NULL DEFAULT '[]',
    tags TEXT NOT NULL DEFAULT '[]',
    nsfw INTEGER NOT NULL DEFAULT 0,
    file_path TEXT NOT NULL DEFAULT '',
    file_name TEXT NOT NULL DEFAULT '',
    sha256 TEXT NOT NULL DEFAULT '',
    bytes INTEGER NOT NULL DEFAULT 0,
    preview_paths TEXT NOT NULL DEFAULT '[]',
    meta_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_loras_status ON loras(status);
CREATE INDEX IF NOT EXISTS idx_loras_created ON loras(created_at DESC);
"""

_VALID_STATUSES = frozenset({"pending", "downloading", "ready", "failed"})
_JSON_LIST_FIELDS = ("trigger_words", "tags", "preview_paths")
_UPDATABLE_FIELDS = frozenset({
    "name", "description", "creator", "base_model", "trigger_words",
    "tags", "nsfw", "file_path", "file_name", "sha256", "bytes",
    "preview_paths", "meta_json", "status", "error", "updated_at",
})


class LoraStore(BaseStore):
    SCHEMA = LORA_SCHEMA

    async def create_pending(
        self,
        lora_id: str,
        *,
        source_url: str,
        civitai_model_id: int,
        civitai_version_id: int | None = None,
    ) -> dict:
        """Create (or reset) a row in 'pending' status. Returns the row.

        Uses INSERT OR REPLACE keyed on the deterministic ``lora-<slug>`` id,
        so re-ingesting the same Civitai URL is idempotent -- it restarts the
        ingest from a clean pending row instead of erroring on a duplicate key.
        """
        now = time.time()
        await self._db.execute(
            """INSERT OR REPLACE INTO loras
               (id, source_url, provider, civitai_model_id, civitai_version_id,
                status, created_at, updated_at)
               VALUES (?, ?, 'civitai', ?, ?, 'pending', ?, ?)""",
            (lora_id, source_url, civitai_model_id, civitai_version_id, now, now),
        )
        await self._db.commit()
        return await self.get(lora_id)

    async def get(self, lora_id: str) -> dict | None:
        self._db.row_factory = aiosqlite.Row
        async with self._db.execute(
            "SELECT * FROM loras WHERE id = ?", (lora_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def list(self, status: str | None = None, limit: int = 100) -> list[dict]:
        self._db.row_factory = aiosqlite.Row
        if status:
            sql = "SELECT * FROM loras WHERE status = ? ORDER BY created_at DESC LIMIT ?"
            params: tuple = (status, limit)
        else:
            sql = "SELECT * FROM loras ORDER BY created_at DESC LIMIT ?"
            params = (limit,)
        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def update(self, lora_id: str, **kwargs) -> None:
        """Update arbitrary columns. List/dict-valued fields are auto-serialised.

        ``trigger_words``, ``tags``, and ``preview_paths`` accept a python
        list (serialised to JSON); ``meta_json`` accepts a python dict.
        """
        if "status" in kwargs and kwargs["status"] not in _VALID_STATUSES:
            raise ValueError(
                f"Invalid status {kwargs['status']!r}; must be one of {sorted(_VALID_STATUSES)}"
            )
        fields = [(k, v) for k, v in kwargs.items() if k in _UPDATABLE_FIELDS]
        if not fields:
            return
        if "updated_at" not in kwargs:
            fields.append(("updated_at", time.time()))
        for i, (k, v) in enumerate(fields):
            if k in _JSON_LIST_FIELDS and isinstance(v, (list, tuple)):
                fields[i] = (k, json.dumps(list(v)))
            elif k == "meta_json" and isinstance(v, dict):
                fields[i] = (k, json.dumps(v))

        set_clause = ", ".join(f"{k} = ?" for k, _ in fields)
        values = [v for _, v in fields]
        values.append(lora_id)
        await self._db.execute(
            f"UPDATE loras SET {set_clause} WHERE id = ?", values
        )
        await self._db.commit()

    async def claim_retry(self, lora_id: str) -> bool:
        """Atomically move a ``failed`` row back to ``pending``.

        Returns True for the caller that won the transition and False for
        everyone else, so a double-clicked Retry (or two concurrent requests)
        can never schedule two ingest jobs writing into the same LoRA
        directory. Read-then-update in the route could not guarantee that.
        """
        cursor = await self._db.execute(
            "UPDATE loras SET status = 'pending', error = '', updated_at = ? "
            "WHERE id = ? AND status = 'failed'",
            (time.time(), lora_id),
        )
        await self._db.commit()
        return cursor.rowcount == 1

    async def delete(self, lora_id: str) -> None:
        await self._db.execute("DELETE FROM loras WHERE id = ?", (lora_id,))
        await self._db.commit()
