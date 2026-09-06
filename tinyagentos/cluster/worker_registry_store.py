"""Persistent store for cluster worker registrations.

Each row tracks one worker across controller restarts so the in-memory
``ClusterManager._workers`` dict (which is lost on process restart) can be
reconstituted.  On restart, all loaded workers are marked "stale" — they are
promoted back to their persisted status when a heartbeat or re-registration
arrives (with an appropriate generation — see ``ClusterManager.generation``).

taOS #640 — Fix 1: persist worker registry (dict lost on restart).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import aiosqlite

from tinyagentos.base_store import BaseStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS cluster_workers (
    name                TEXT NOT NULL UNIQUE,
    url                 TEXT NOT NULL,
    hardware            TEXT NOT NULL DEFAULT '{}',
    backends            TEXT NOT NULL DEFAULT '[]',
    models              TEXT NOT NULL DEFAULT '[]',
    available_models    TEXT NOT NULL DEFAULT '[]',
    capabilities        TEXT NOT NULL DEFAULT '[]',
    status              TEXT NOT NULL DEFAULT 'stale',
    last_heartbeat      REAL NOT NULL DEFAULT 0,
    registered_at       REAL NOT NULL DEFAULT 0,
    load                REAL NOT NULL DEFAULT 0.0,
    platform            TEXT NOT NULL DEFAULT '',
    tier_id             TEXT NOT NULL DEFAULT '',
    potential_capabilities TEXT NOT NULL DEFAULT '[]',
    kv_cache_quant_support          TEXT NOT NULL DEFAULT '["fp16"]',
    kv_cache_quant_k_support        TEXT NOT NULL DEFAULT '["fp16"]',
    kv_cache_quant_v_support        TEXT NOT NULL DEFAULT '["fp16"]',
    kv_cache_quant_boundary_layer_protect INTEGER NOT NULL DEFAULT 0,
    worker_url          TEXT,
    signing_key         BLOB,
    tls_cert_provider   TEXT,
    host_lan_ip         TEXT,
    storage_cap_bytes   INTEGER NOT NULL DEFAULT 0,
    storage_used_bytes  INTEGER NOT NULL DEFAULT 0,
    bytes_deduped_total INTEGER NOT NULL DEFAULT 0,
    worker_lxc_image_version TEXT,
    degraded            INTEGER NOT NULL DEFAULT 0,
    degraded_reason     TEXT,
    free_vram_mb        INTEGER,
    used_vram_mb        INTEGER
);

-- Controller generation table for split-brain protection (Fix 2).
CREATE TABLE IF NOT EXISTS cluster_generation (
    id       INTEGER PRIMARY KEY CHECK (id = 1),
    value    INTEGER NOT NULL DEFAULT 1
);
"""


class WorkerRegistryStore(BaseStore):
    """SQLite-backed store for worker registration records.

    Persistence layer for Fix 1 (#640). The ``ClusterManager`` reads from
    this store on startup to recover its in-memory registry after a restart,
    and writes on every register/heartbeat/unregister so the DB stays in
    sync with the running state.
    """

    SCHEMA = SCHEMA

    async def init(self) -> None:
        await super().init()
        if self._db is not None:
            self._db.row_factory = aiosqlite.Row
        # Ensure the generation row exists (INSERT OR IGNORE).
        if self._db is not None:
            await self._db.execute(
                "INSERT OR IGNORE INTO cluster_generation (id, value) VALUES (1, 1)"
            )
            await self._db.commit()

    # ------------------------------------------------------------------
    # Worker CRUD
    # ------------------------------------------------------------------

    async def upsert_worker(self, info: dict) -> None:
        """Insert or update a worker row with the full WorkerInfo snapshot."""
        if self._db is None:
            raise RuntimeError("WorkerRegistryStore not initialised")
        await self._db.execute(
            """
            INSERT INTO cluster_workers (
                name, url, hardware, backends, models, available_models,
                capabilities, status, last_heartbeat, registered_at, load,
                platform, tier_id, potential_capabilities,
                kv_cache_quant_support, kv_cache_quant_k_support,
                kv_cache_quant_v_support, kv_cache_quant_boundary_layer_protect,
                worker_url, signing_key, tls_cert_provider,
                host_lan_ip, storage_cap_bytes, storage_used_bytes,
                bytes_deduped_total, worker_lxc_image_version,
                degraded, degraded_reason, free_vram_mb, used_vram_mb
            ) VALUES (
                :name, :url, :hardware, :backends, :models, :available_models,
                :capabilities, :status, :last_heartbeat, :registered_at, :load,
                :platform, :tier_id, :potential_capabilities,
                :kv_cache_quant_support, :kv_cache_quant_k_support,
                :kv_cache_quant_v_support, :kv_cache_quant_boundary_layer_protect,
                :worker_url, :signing_key, :tls_cert_provider,
                :host_lan_ip, :storage_cap_bytes, :storage_used_bytes,
                :bytes_deduped_total, :worker_lxc_image_version,
                :degraded, :degraded_reason, :free_vram_mb, :used_vram_mb
            )
            ON CONFLICT(name) DO UPDATE SET
                url              = excluded.url,
                hardware         = excluded.hardware,
                backends         = excluded.backends,
                models           = excluded.models,
                available_models = excluded.available_models,
                capabilities     = excluded.capabilities,
                status           = excluded.status,
                last_heartbeat   = excluded.last_heartbeat,
                registered_at    = excluded.registered_at,
                load             = excluded.load,
                platform         = excluded.platform,
                tier_id          = excluded.tier_id,
                potential_capabilities = excluded.potential_capabilities,
                kv_cache_quant_support = excluded.kv_cache_quant_support,
                kv_cache_quant_k_support = excluded.kv_cache_quant_k_support,
                kv_cache_quant_v_support = excluded.kv_cache_quant_v_support,
                kv_cache_quant_boundary_layer_protect = excluded.kv_cache_quant_boundary_layer_protect,
                worker_url       = excluded.worker_url,
                signing_key      = excluded.signing_key,
                tls_cert_provider = excluded.tls_cert_provider,
                host_lan_ip      = excluded.host_lan_ip,
                storage_cap_bytes = excluded.storage_cap_bytes,
                storage_used_bytes = excluded.storage_used_bytes,
                bytes_deduped_total = excluded.bytes_deduped_total,
                worker_lxc_image_version = excluded.worker_lxc_image_version,
                degraded         = excluded.degraded,
                degraded_reason  = excluded.degraded_reason,
                free_vram_mb     = excluded.free_vram_mb,
                used_vram_mb     = excluded.used_vram_mb
            """,
            info,
        )
        await self._db.commit()

    async def remove_worker(self, name: str) -> None:
        """Delete a worker row."""
        if self._db is None:
            raise RuntimeError("WorkerRegistryStore not initialised")
        await self._db.execute(
            "DELETE FROM cluster_workers WHERE name = ?", (name,)
        )
        await self._db.commit()

    async def load_all(self) -> list[dict]:
        """Return all persisted worker rows as a list of dicts."""
        if self._db is None:
            raise RuntimeError("WorkerRegistryStore not initialised")
        cursor = await self._db.execute("SELECT * FROM cluster_workers")
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Generation counter (Fix 2: split-brain protection)
    # ------------------------------------------------------------------

    async def increment_generation(self) -> int:
        """Atomically increment the generation counter and return the new value."""
        if self._db is None:
            raise RuntimeError("WorkerRegistryStore not initialised")
        cursor = await self._db.execute(
            "UPDATE cluster_generation SET value = value + 1 WHERE id = 1 "
            "RETURNING value"
        )
        row = await cursor.fetchone()
        await self._db.commit()
        return row["value"] if row else 1

    async def current_generation(self) -> int:
        """Return the current generation counter value."""
        if self._db is None:
            raise RuntimeError("WorkerRegistryStore not initialised")
        cursor = await self._db.execute(
            "SELECT value FROM cluster_generation WHERE id = 1"
        )
        row = await cursor.fetchone()
        return row["value"] if row else 1
