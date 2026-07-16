"""Tests for GPU lease API (taOS #893)."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from test_routes_cluster_pairing import pair_worker, sign_worker_request

from tinyagentos.cluster.manager import ClusterManager
from tinyagentos.cluster.worker_protocol import GpuLease, WorkerInfo


async def _register_worker(client, app, name, url, capabilities=None, hardware=None):
    """Pair and register a worker over signed HTTP so it clears the worker
    HMAC gate on /api/cluster/workers. Returns the worker's signing key."""
    await app.state.cluster_pairing.init()
    key = await pair_worker(client, app, name, url)
    body = {"name": name, "url": url, "capabilities": capabilities or []}
    if hardware is not None:
        body["hardware"] = hardware
    body_bytes = json.dumps(body).encode()
    path = "/api/cluster/workers"
    headers = sign_worker_request(key, name, "POST", path, body_bytes)
    headers["content-type"] = "application/json"
    resp = await client.post(path, content=body_bytes, headers=headers)
    assert resp.status_code == 200, resp.text
    return key


async def _heartbeat(client, key, name, **fields):
    """Send a signed heartbeat for `name` carrying the given extra fields."""
    body = {"name": name, **fields}
    body_bytes = json.dumps(body).encode()
    path = "/api/cluster/heartbeat"
    headers = sign_worker_request(key, name, "POST", path, body_bytes)
    headers["content-type"] = "application/json"
    resp = await client.post(path, content=body_bytes, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp


def _auth(app) -> dict:
    """Authorization header carrying the app's local token.

    The lease endpoints require a trusted principal (admin session, local
    token, or worker HMAC); the local token is the simplest in tests and
    stands in for a same-host controller-side caller (dispatcher/agent)."""
    return {"Authorization": f"Bearer {app.state.auth.get_local_token()}"}


@pytest.mark.asyncio
async def test_claim_lease_success(client, app):
    """Claiming a lease on an online worker with enough VRAM returns 200."""
    # Register a worker with free VRAM
    key = await _register_worker(
        client, app, "gpu-node", "http://10.0.0.1:9000",
        capabilities=["llm-chat"],
        hardware={"gpu": {"model": "GTX 1080", "vram_mb": 8192}},
    )
    # Send a heartbeat with free VRAM
    await _heartbeat(client, key, "gpu-node", free_vram_mb=6000, used_vram_mb=2000)

    resp = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "gpu-node:gpu-cuda-0",
        "caller": "skald-dispatcher",
        "ttl_seconds": 30,
        "required_vram_mb": 4000,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "claimed"
    assert data["lease_id"].startswith("l_")
    assert data["resource_id"] == "gpu-node:gpu-cuda-0"
    assert data["ttl_seconds"] == 30
    assert data["required_vram_mb"] == 4000
    assert "expires_at" in data


@pytest.mark.asyncio
async def test_claim_lease_insufficient_vram(client, app):
    """Claiming with required_vram_mb > free_vram_mb returns 409."""
    key = await _register_worker(client, app, "gpu-node", "http://10.0.0.1:9000", capabilities=["llm-chat"])
    await _heartbeat(client, key, "gpu-node", free_vram_mb=1000, used_vram_mb=7000)

    resp = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "gpu-node:gpu-cuda-0",
        "required_vram_mb": 8000,
    })
    assert resp.status_code == 409
    assert "unavailable" in resp.json()["error"]


@pytest.mark.asyncio
async def test_claim_lease_already_leased(client, app):
    """A second claim on the same resource returns 409 with the existing lease info."""
    key = await _register_worker(client, app, "gpu-node", "http://10.0.0.1:9000", capabilities=["llm-chat"])
    await _heartbeat(client, key, "gpu-node", free_vram_mb=8000, used_vram_mb=0)

    # First claim succeeds
    resp1 = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "gpu-node:gpu-cuda-0",
        "caller": "skald-dispatcher",
        "ttl_seconds": 30,
    })
    assert resp1.status_code == 200
    lease_id = resp1.json()["lease_id"]

    # Second claim on same resource fails
    resp2 = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "gpu-node:gpu-cuda-0",
        "caller": "a2a-agent:extract",
    })
    assert resp2.status_code == 409
    err = resp2.json()
    assert err["error"] == "resource already leased"
    assert err["lease_id"] == lease_id
    assert err["caller"] == "skald-dispatcher"


@pytest.mark.asyncio
async def test_claim_lease_unknown_vram_is_granted(client, app):
    """A worker that never reported VRAM (free_vram_mb stays None, e.g.
    RK3588/Apple Silicon/CPU-only) must not be refused on VRAM grounds."""
    await _register_worker(client, app, "cpu-node", "http://10.0.0.2:9000", capabilities=["llm-chat"])
    # No heartbeat with free_vram_mb ever sent -- it stays at its default (None).

    resp = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "cpu-node:gpu-cuda-0",
        "required_vram_mb": 4000,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "claimed"


@pytest.mark.asyncio
async def test_claim_lease_worker_offline(client, app):
    """Claiming against an offline worker returns 409."""
    # Worker registered but no heartbeat ever sent — marked offline after
    # monitor_loop sweep.  The monitor_loop hasn't run in this test
    # context (no app startup), so the worker is technically "online" from
    # registration.  To test offline, claim against a nonexistent worker.
    resp = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "ghost:gpu-cuda-0",
        "required_vram_mb": 4000,
    })
    assert resp.status_code == 409
    assert "unavailable" in resp.json()["error"]


@pytest.mark.asyncio
async def test_claim_lease_malformed_resource_id(client, app):
    """A resource_id without a colon returns 409."""
    resp = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "bogus",
    })
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_release_lease_idempotent(client, app):
    """Releasing a lease returns 200; releasing again is also 200."""
    key = await _register_worker(client, app, "gpu-node", "http://10.0.0.1:9000", capabilities=["llm-chat"])
    await _heartbeat(client, key, "gpu-node", free_vram_mb=8000)

    claim = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "gpu-node:gpu-cuda-0",
    })
    lease_id = claim.json()["lease_id"]

    # First release
    resp1 = await client.post("/api/cluster/leases/release", headers=_auth(app), json={
        "lease_id": lease_id,
    })
    assert resp1.status_code == 200
    assert resp1.json()["status"] == "released"

    # Second release (idempotent)
    resp2 = await client.post("/api/cluster/leases/release", headers=_auth(app), json={
        "lease_id": lease_id,
    })
    assert resp2.status_code == 200


@pytest.mark.asyncio
async def test_release_lease_unknown(client, app):
    """Releasing an unknown lease_id is still 200 (idempotent)."""
    resp = await client.post("/api/cluster/leases/release", headers=_auth(app), json={
        "lease_id": "l_doesnotexist",
    })
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_renew_lease_success(client, app):
    """Renewing an active lease extends its TTL."""
    key = await _register_worker(client, app, "gpu-node", "http://10.0.0.1:9000", capabilities=["llm-chat"])
    await _heartbeat(client, key, "gpu-node", free_vram_mb=8000)

    claim = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "gpu-node:gpu-cuda-0",
        "ttl_seconds": 10,
    })
    lease_id = claim.json()["lease_id"]
    original_expiry = claim.json()["expires_at"]

    resp = await client.post("/api/cluster/leases/renew", headers=_auth(app), json={
        "lease_id": lease_id,
        "ttl_seconds": 60,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "renewed"
    assert resp.json()["lease_id"] == lease_id
    assert resp.json()["expires_at"] > original_expiry


@pytest.mark.asyncio
async def test_renew_lease_expired(client, app):
    """Renewing an expired lease returns 409."""
    key = await _register_worker(client, app, "gpu-node", "http://10.0.0.1:9000", capabilities=["llm-chat"])
    await _heartbeat(client, key, "gpu-node", free_vram_mb=8000)

    claim = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "gpu-node:gpu-cuda-0",
        "ttl_seconds": 0.001,  # effectively instant
    })
    lease_id = claim.json()["lease_id"]

    # Wait a moment for it to expire
    time.sleep(0.1)

    resp = await client.post("/api/cluster/leases/renew", headers=_auth(app), json={
        "lease_id": lease_id,
        "ttl_seconds": 30,
    })
    assert resp.status_code == 409
    assert "expired" in resp.json()["error"]


@pytest.mark.asyncio
async def test_list_leases(client, app):
    """GET /leases returns active leases only."""
    key = await _register_worker(client, app, "gpu-node", "http://10.0.0.1:9000", capabilities=["llm-chat"])
    await _heartbeat(client, key, "gpu-node", free_vram_mb=8000)

    # No leases yet
    resp = await client.get("/api/cluster/leases", headers=_auth(app))
    assert resp.status_code == 200
    assert resp.json()["count"] == 0

    # Claim one
    claim = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "gpu-node:gpu-cuda-0",
        "caller": "test",
        "ttl_seconds": 30,
    })
    assert claim.status_code == 200

    resp = await client.get("/api/cluster/leases", headers=_auth(app))
    assert resp.status_code == 200
    assert resp.json()["count"] == 1
    lease = resp.json()["leases"][0]
    assert lease["caller"] == "test"
    assert lease["resource_id"] == "gpu-node:gpu-cuda-0"
    assert "lease_id" in lease


@pytest.mark.asyncio
async def test_expired_lease_returns_409_on_claim(client, app):
    """After a lease expires, a new claim on the same resource succeeds."""
    key = await _register_worker(client, app, "gpu-node", "http://10.0.0.1:9000", capabilities=["llm-chat"])
    await _heartbeat(client, key, "gpu-node", free_vram_mb=8000)

    # Claim with very short TTL
    await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "gpu-node:gpu-cuda-0",
        "ttl_seconds": 0.001,
    })

    time.sleep(0.1)

    # The lease is now expired; a new claim succeeds
    resp = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "gpu-node:gpu-cuda-0",
        "caller": "second-caller",
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "claimed"


@pytest.mark.asyncio
async def test_lease_endpoints_require_auth(client, app):
    """Unauthenticated callers are rejected on every lease endpoint.

    Without this gate any LAN process could list and release every lease,
    re-enabling the concurrent-GPU-load the lease system prevents.
    """
    # Drop the default admin session so the requests are genuinely anonymous.
    client.cookies.clear()
    rejected = {401, 403}
    claim = await client.post("/api/cluster/leases/claim", json={
        "resource_id": "gpu-node:gpu-cuda-0",
    })
    assert claim.status_code in rejected
    release = await client.post("/api/cluster/leases/release", json={
        "lease_id": "l_whatever",
    })
    assert release.status_code in rejected
    renew = await client.post("/api/cluster/leases/renew", json={
        "lease_id": "l_whatever",
    })
    assert renew.status_code in rejected
    listing = await client.get("/api/cluster/leases")
    assert listing.status_code in rejected


@pytest.mark.asyncio
async def test_claim_rejects_nonpositive_ttl(client, app):
    """A zero/negative ttl is rejected (422) rather than minting a lease that
    is already expired and grants no exclusivity."""
    resp = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "gpu-node:gpu-cuda-0",
        "ttl_seconds": 0,
    })
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_claim_rejects_negative_vram(client, app):
    """A negative required_vram_mb is rejected (422) rather than silently
    bypassing the VRAM guard."""
    resp = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "gpu-node:gpu-cuda-0",
        "required_vram_mb": -1,
    })
    assert resp.status_code == 422


# ── taOS #1705: DELETE /worker endpoint leases ─────────────────────────


@pytest.mark.asyncio
async def test_unregister_worker_releases_leases(client, app):
    """DELETE /api/cluster/workers/{name} releases all GPU leases held
    by that worker (taOS #1705)."""
    # Register a worker with enough VRAM
    key = await _register_worker(
        client, app, "gpu-node", "http://10.0.0.1:9000",
        capabilities=["llm-chat"],
        hardware={"gpu": {"model": "GTX 1080", "vram_mb": 8192}},
    )
    await _heartbeat(client, key, "gpu-node", free_vram_mb=8000, used_vram_mb=0)

    # Claim two leases
    claim1 = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "gpu-node:gpu-cuda-0",
        "caller": "test",
        "ttl_seconds": 60,
    })
    assert claim1.status_code == 200

    claim2 = await client.post("/api/cluster/leases/claim", headers=_auth(app), json={
        "resource_id": "gpu-node:gpu-cuda-1",
        "caller": "test",
        "ttl_seconds": 60,
    })
    assert claim2.status_code == 200

    # Verify both leases are present
    list_resp = await client.get("/api/cluster/leases", headers=_auth(app))
    assert list_resp.json()["count"] == 2

    # Unregister the worker — this must release its leases
    resp = await client.delete("/api/cluster/workers/gpu-node")
    assert resp.status_code == 200
    assert resp.json()["status"] == "removed"

    # Verify all leases are gone
    list_resp = await client.get("/api/cluster/leases", headers=_auth(app))
    assert list_resp.json()["count"] == 0

    await app.state.cluster_pairing.close()


# ── ClusterManager unit tests ──────────────────────────────────────────


def _worker(name, free_vram=None):
    w = WorkerInfo(
        name=name,
        url=f"http://{name}:9000",
        capabilities=["llm-chat"],
        free_vram_mb=free_vram,
    )
    w.status = "online"
    w.last_heartbeat = time.time()
    return w


@pytest.mark.asyncio
class TestClusterManagerLeases:
    async def test_claim_success(self):
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=6000)

        lease = await mgr.claim_lease(
            "gpu-node:gpu-cuda-0",
            caller="test",
            ttl_seconds=30,
            required_vram_mb=4000,
        )
        assert lease is not None
        assert lease.lease_id.startswith("l_")
        assert lease.resource_id == "gpu-node:gpu-cuda-0"
        assert lease.caller == "test"
        assert lease.required_vram_mb == 4000

    async def test_claim_fails_insufficient_vram(self):
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=1000)

        lease = await mgr.claim_lease(
            "gpu-node:gpu-cuda-0",
            required_vram_mb=8000,
        )
        assert lease is None

    async def test_claim_unknown_vram_is_granted(self):
        """free_vram_mb is None (never reported) -- must not be refused."""
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=None)

        lease = await mgr.claim_lease(
            "gpu-node:gpu-cuda-0",
            required_vram_mb=8000,
        )
        assert lease is not None

    async def test_claim_fails_already_leased(self):
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=8000)

        first = await mgr.claim_lease("gpu-node:gpu-cuda-0", caller="first")
        assert first is not None

        second = await mgr.claim_lease("gpu-node:gpu-cuda-0", caller="second")
        assert second is None

    async def test_claim_fails_worker_offline(self):
        mgr = ClusterManager()
        w = _worker("gpu-node", free_vram=8000)
        w.status = "offline"
        mgr._workers["gpu-node"] = w

        lease = await mgr.claim_lease("gpu-node:gpu-cuda-0")
        assert lease is None

    async def test_claim_fails_missing_worker(self):
        mgr = ClusterManager()
        lease = await mgr.claim_lease("nonexistent:gpu-cuda-0")
        assert lease is None

    async def test_release_idempotent(self):
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=8000)

        lease = await mgr.claim_lease("gpu-node:gpu-cuda-0")
        assert lease is not None

        # Releasing works
        assert await mgr.release_lease(lease.lease_id) is True

        # Releasing again (idempotent)
        assert await mgr.release_lease(lease.lease_id) is True

    async def test_release_unknown(self):
        mgr = ClusterManager()
        assert await mgr.release_lease("l_nonexistent") is True

    async def test_renew_active(self):
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=8000)

        lease = await mgr.claim_lease("gpu-node:gpu-cuda-0", ttl_seconds=10)
        original_expiry = lease.expires_at

        renewed = await mgr.renew_lease(lease.lease_id, ttl_seconds=60)
        assert renewed is not None
        assert renewed.expires_at > original_expiry

    async def test_renew_expired(self):
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=8000)

        lease = await mgr.claim_lease("gpu-node:gpu-cuda-0", ttl_seconds=0.001)
        time.sleep(0.1)

        renewed = await mgr.renew_lease(lease.lease_id, ttl_seconds=30)
        assert renewed is None

    async def test_renew_unknown(self):
        mgr = ClusterManager()
        assert await mgr.renew_lease("l_nonexistent") is None

    async def test_get_leases_active_only(self):
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=8000)

        await mgr.claim_lease("gpu-node:gpu-cuda-0", ttl_seconds=30)
        assert len(mgr.get_leases()) == 1

        # Add an expired lease manually
        mgr._leases["l_expired"] = GpuLease(
            lease_id="l_expired",
            resource_id="gpu-node:gpu-cuda-0",
            expires_at=0,
        )
        # get_leases only returns active (non-expired)
        assert len(mgr.get_leases()) == 1

    async def test_sweep_removes_expired(self):
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=8000)

        # Claim with instant TTL
        lease = await mgr.claim_lease("gpu-node:gpu-cuda-0", ttl_seconds=0.001)
        time.sleep(0.1)

        # Before sweep, expired lease still in dict
        assert lease.lease_id in mgr._leases

        mgr._sweep_expired_leases()
        assert lease.lease_id not in mgr._leases
        assert len(mgr.get_leases()) == 0

    async def test_claim_after_release_succeeds(self):
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=8000)

        first = await mgr.claim_lease("gpu-node:gpu-cuda-0", caller="first")
        await mgr.release_lease(first.lease_id)

        second = await mgr.claim_lease("gpu-node:gpu-cuda-0", caller="second")
        assert second is not None
        assert second.caller == "second"

    async def test_claim_concurrent_same_resource_exactly_one_succeeds(self):
        """Many concurrent claim() calls for the same resource must yield
        exactly one lease -- the find-existing/check-VRAM/store sequence
        is serialized by ClusterManager._lease_lock."""
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=8000)

        results = await asyncio.gather(*[
            mgr.claim_lease("gpu-node:gpu-cuda-0", caller=f"caller-{i}")
            for i in range(20)
        ])
        successes = [r for r in results if r is not None]
        assert len(successes) == 1
        assert len(mgr.get_leases()) == 1

    async def test_claim_serialized_by_lease_lock(self):
        """A claim() call must actually wait for _lease_lock -- proves the
        atomic critical section is wired up, not just present in name."""
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=8000)

        async with mgr._lease_lock:
            task = asyncio.create_task(
                mgr.claim_lease("gpu-node:gpu-cuda-0", caller="blocked")
            )
            await asyncio.sleep(0.05)
            assert not task.done()

        result = await task
        assert result is not None

    # ── taOS #1705: unregister releases leases ─────────────────────────

    async def test_unregister_releases_all_leases(self):
        """Unregistering a worker releases all of its active GPU leases."""
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=8000)

        # Claim several leases
        lease1 = await mgr.claim_lease("gpu-node:gpu-cuda-0", caller="test")
        lease2 = await mgr.claim_lease("gpu-node:gpu-cuda-1", caller="test")
        assert lease1 is not None
        assert lease2 is not None
        assert len(mgr.get_leases()) == 2

        # Unregister — all leases must be gone
        assert await mgr.unregister_worker("gpu-node") is True
        assert len(mgr.get_leases()) == 0
        assert lease1.lease_id not in mgr._leases
        assert lease2.lease_id not in mgr._leases

    async def test_unregister_unknown_worker_noop(self):
        """Unregistering an unknown worker returns False and touches no leases."""
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=8000)
        await mgr.claim_lease("gpu-node:gpu-cuda-0", caller="test")
        assert len(mgr.get_leases()) == 1

        assert await mgr.unregister_worker("ghost") is False
        assert len(mgr.get_leases()) == 1  # existing lease untouched

    async def test_unregister_one_worker_spares_another(self):
        """Unregistering worker A leaves worker B's leases untouched."""
        mgr = ClusterManager()
        mgr._workers["gpu-a"] = _worker("gpu-a", free_vram=8000)
        mgr._workers["gpu-b"] = _worker("gpu-b", free_vram=8000)

        lease_a = await mgr.claim_lease("gpu-a:gpu-cuda-0", caller="test")
        lease_b = await mgr.claim_lease("gpu-b:gpu-cuda-0", caller="test")
        assert lease_a is not None
        assert lease_b is not None

        assert await mgr.unregister_worker("gpu-a") is True
        assert len(mgr.get_leases()) == 1
        assert lease_b.lease_id in mgr._leases
        assert lease_a.lease_id not in mgr._leases

    async def test_reclaim_after_unregister(self):
        """After unregister releases leases, a new claim succeeds."""
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=8000)

        await mgr.claim_lease("gpu-node:gpu-cuda-0", caller="first")
        assert await mgr.unregister_worker("gpu-node") is True

        # Re-register and claim again
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=8000)
        lease = await mgr.claim_lease("gpu-node:gpu-cuda-0", caller="second")
        assert lease is not None
        assert lease.caller == "second"

    async def test_unregister_releases_leases_atomically(self):
        """Unregister holds _lease_lock so concurrent claims see a
        consistent view — they cannot race with the lease release."""
        mgr = ClusterManager()
        mgr._workers["gpu-node"] = _worker("gpu-node", free_vram=8000)

        # Hold the lock so unregister blocks on it
        async with mgr._lease_lock:
            unregister_task = asyncio.create_task(
                mgr.unregister_worker("gpu-node")
            )
            await asyncio.sleep(0.05)
            assert not unregister_task.done()

        # Lock released — unregister completes
        assert await unregister_task
        assert len(mgr.get_leases()) == 0
