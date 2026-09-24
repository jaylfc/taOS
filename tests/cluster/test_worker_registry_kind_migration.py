"""The `kind` column (worker vs device) must be retrofitted onto a
cluster_workers database created BEFORE taOSusb BLE pairing (S1) existed,
via the guarded _post_init ALTER TABLE -- mirrors
tests/cluster/test_pairing_store.py's blocked/revoked migration test."""
from __future__ import annotations

import aiosqlite
import pytest

from tinyagentos.cluster.worker_registry_store import WorkerRegistryStore


@pytest.mark.asyncio
async def test_kind_column_migration_over_existing_db(tmp_path):
    path = tmp_path / "cluster_workers.db"
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        await db.execute(
            """
            CREATE TABLE cluster_workers (
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
                tier_id             TEXT NOT NULL DEFAULT ''
            )
            """
        )
        await db.execute(
            "INSERT INTO cluster_workers (name, url) VALUES ('old-worker', 'http://10.0.0.1:6970')"
        )
        await db.commit()

    store = WorkerRegistryStore(path)
    await store.init()
    try:
        import sqlite3
        conn = sqlite3.connect(str(path))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(cluster_workers)").fetchall()}
        conn.close()
        assert "kind" in cols

        rows = await store.load_all()
        row = next(r for r in rows if r["name"] == "old-worker")
        # A pre-existing row (registered before kind existed) defaults to
        # 'worker' -- it must never silently become a device.
        assert row["kind"] == "worker"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_upsert_worker_persists_kind(tmp_path):
    store = WorkerRegistryStore(tmp_path / "cluster_workers.db")
    await store.init()
    try:
        await store.upsert_worker({
            "name": "device-1", "url": "", "hardware": "{}", "backends": "[]",
            "models": "[]", "available_models": "[]", "capabilities": "[]",
            "status": "online", "last_heartbeat": 0, "registered_at": 0,
            "load": 0.0, "platform": "taosusb", "tier_id": "",
            "potential_capabilities": "[]", "kv_cache_quant_support": '["fp16"]',
            "kv_cache_quant_k_support": '["fp16"]', "kv_cache_quant_v_support": '["fp16"]',
            "kv_cache_quant_boundary_layer_protect": 0, "worker_url": None,
            "signing_key": b"\x01" * 32, "tls_cert_provider": None,
            "host_lan_ip": None, "storage_cap_bytes": 0, "storage_used_bytes": 0,
            "bytes_deduped_total": 0, "worker_lxc_image_version": None,
            "degraded": 0, "degraded_reason": None, "free_vram_mb": None,
            "used_vram_mb": None, "kind": "device",
        })
        rows = await store.load_all()
        row = next(r for r in rows if r["name"] == "device-1")
        assert row["kind"] == "device"
    finally:
        await store.close()
