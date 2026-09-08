"""Q2-2 cluster manager hardening nit tests.

Covers four fixes from docs/audit/library-replacement-audit-2026-09-pass2.md (Q2-2):

1. _format_hw raises TypeError on non-int ram/vram        -> coerce + route guard
2. _ever_seen.add runs before the generation guards         -> move after guards
3. routes/cluster.py uses app.state.notif_store            -> notifications
4. stop() never drains _background_tasks                  -> gather with timeout
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tinyagentos.cluster.manager import ClusterManager, _format_hw
from tinyagentos.cluster.worker_protocol import WorkerInfo

# ── 1. _format_hw coercion ────────────────────────────────────────────

def test_format_hw_non_int_ram_does_not_raise():
    """Worker-supplied ram_mb='lots' must not raise TypeError in _format_hw."""
    result = _format_hw({"ram_mb": "lots"})
    assert isinstance(result, str)


def test_format_hw_non_int_vram_does_not_raise():
    """Worker-supplied gpu.vram_mb='lots' must not raise TypeError."""
    result = _format_hw({
        "ram_mb": 16384,
        "gpu": {"type": "nvidia", "vram_mb": "lots"},
    })
    assert isinstance(result, str)


def test_format_hw_none_ram_is_safe():
    result = _format_hw({"ram_mb": None})
    assert isinstance(result, str)


def test_format_hw_non_dict_gpu_is_safe():
    result = _format_hw({"ram_mb": 16384, "gpu": "not-a-dict"})
    assert isinstance(result, str)


# ── 2. _ever_seen.add after guards ────────────────────────────────────

@pytest.mark.asyncio
class TestEverSeenOrdering:
    async def test_stale_generation_does_not_suppress_join_notification(self):
        """A rejected stale-generation registration must not mark the worker
        as 'ever seen', so a subsequent valid registration fires worker.join
        (PROVEN in audit pass-2 Q2-2)."""
        notif = AsyncMock()
        mgr = ClusterManager(notifications=notif)
        mgr._generation = 1

        w = WorkerInfo(name="w1", url="http://w1:9000", capabilities=["chat"])

        # Stale generation -- rejected before _ever_seen.add
        ok, reason = await mgr.register_worker(w, generation=999)
        assert ok is False
        assert reason == "stale_generation"

        # Valid registration -- should fire worker.join since the stale
        # registration did not poison _ever_seen.
        ok, reason = await mgr.register_worker(w, generation=1)
        assert ok is True
        assert reason == ""

        join_calls = [
            c for c in notif.emit_event.await_args_list
            if c.args and c.args[0] == "worker.join"
        ]
        assert len(join_calls) == 1, (
            f"Expected exactly 1 worker.join notification, got {len(join_calls)}"
        )

        # Clean up background tasks (model promotion is fire-and-forget).
        if mgr._background_tasks:
            await asyncio.gather(
                *mgr._background_tasks, return_exceptions=True
            )


# ── 3. notif_store typo (routes/cluster.py) ───────────────────────────

def test_surface_storage_backup_emits_notification():
    """_surface_storage_backup must read app.state.notifications (not
    notif_store) so the storage-backup notification actually fires."""
    from tinyagentos.routes.cluster import _surface_storage_backup

    mock_notif = MagicMock()
    mock_notif.add = AsyncMock()

    app = SimpleNamespace(
        state=SimpleNamespace(
            notifications=mock_notif,
            data_dir=None,  # skip the file-writing path
        )
    )

    asyncio.run(
        _surface_storage_backup(
            app,
            "test-worker",
            {
                "backed_up_pool": "old-pool",
                "original_name": "taos-worker-pool",
                "timestamp_utc": "2026-01-01",
                "reason": "migration",
            },
        )
    )

    mock_notif.add.assert_awaited_once()
    call = mock_notif.add.await_args
    assert "storage pool backed up" in call.args[0]
    assert call.kwargs.get("level") == "warning"


# ── 4. stop() drains _background_tasks ─────────────────────────────────

@pytest.mark.asyncio
class TestStopDrainsTasks:
    async def test_stop_awaits_pending_background_tasks(self):
        """stop() must drain _background_tasks so fire-and-forget tasks
        complete before shutdown (R2-25)."""
        mgr = ClusterManager()
        mgr._monitor_task = None

        flag = {"done": False}

        async def _task():
            await asyncio.sleep(0.05)
            flag["done"] = True

        task = asyncio.create_task(_task())
        mgr._background_tasks.add(task)
        task.add_done_callback(mgr._background_tasks.discard)

        await mgr.stop()

        assert flag["done"] is True
        assert len(mgr._background_tasks) == 0


# ── Route-level: heartbeat with non-int hardware → 400 ────────────────

@pytest.mark.asyncio
class TestHeartbeatHardwareValidation:
    async def test_heartbeat_non_int_ram_returns_400(self, client, app):
        """Heartbeat carrying ram_mb='lots' must return 400, not 500."""
        from test_routes_cluster import pair_worker, sign_worker_request

        key = await pair_worker(
            client, app, "bad-hw-worker", "http://10.0.0.1:9000"
        )
        # Register with valid (no) hardware
        reg_body = json.dumps({
            "name": "bad-hw-worker",
            "url": "http://10.0.0.1:9000",
        }).encode()
        headers = sign_worker_request(
            key, "bad-hw-worker", "POST", "/api/cluster/workers", reg_body
        )
        resp = await client.post(
            "/api/cluster/workers", content=reg_body,
            headers={**headers, "content-type": "application/json"},
        )
        assert resp.status_code == 200, resp.text

        # Heartbeat with non-int ram_mb
        hb_body = json.dumps({
            "name": "bad-hw-worker",
            "hardware": {"ram_mb": "lots"},
        }).encode()
        hb_headers = sign_worker_request(
            key, "bad-hw-worker", "POST", "/api/cluster/heartbeat", hb_body
        )
        resp = await client.post(
            "/api/cluster/heartbeat", content=hb_body,
            headers={**hb_headers, "content-type": "application/json"},
        )
        assert resp.status_code == 400, resp.text
        await app.state.cluster_pairing.close()
