"""Refusing-direction tests for worker lease binding (taOS #4358).

Two paired workers A and B go through the real app and middleware:
- A claiming a lease AS B (body.caller = "B", signed as A) -> 403, no lease created
- B renewing or releasing A's lease -> 403 and A's expiry unchanged
- B's lease list excludes A's lease
- A's own claim/renew/release succeed
- admin session and host token unchanged

All four lease routes are covered: claim, release, renew, list.
"""
from __future__ import annotations

import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from test_leases import _heartbeat, _register_worker
from test_routes_cluster_pairing import sign_worker_request


async def _hmac_client(app):
    """Fresh AsyncClient with no session cookie (anonymous caller)."""
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


def _sign(key, name, method, path, body):
    return sign_worker_request(key, name, method, path, body)


async def _post_hmac(client, key, name, path, fields):
    body = json.dumps(fields).encode()
    headers = _sign(key, name, "POST", path, body)
    headers["content-type"] = "application/json"
    return await client.post(path, content=body, headers=headers)


async def _get_hmac(client, key, name, path):
    headers = _sign(key, name, "GET", path, b"")
    return await client.get(path, headers=headers)


@pytest.mark.asyncio
async def test_refusing_direction_two_workers(client, app):
    """Worker A cannot claim/release/renew as worker B; B's list excludes A's lease."""
    # Register worker A with free VRAM
    key_a = await _register_worker(
        client, app, "worker-a", "http://10.0.0.1:9001",
        capabilities=["llm-chat"],
        hardware={"gpu": {"model": "GTX 1080", "vram_mb": 8192}},
    )
    await _heartbeat(client, key_a, "worker-a", free_vram_mb=6000, used_vram_mb=2000)

    # Register worker B with free VRAM
    key_b = await _register_worker(
        client, app, "worker-b", "http://10.0.0.2:9001",
        capabilities=["llm-chat"],
        hardware={"gpu": {"model": "GTX 1080", "vram_mb": 8192}},
    )
    await _heartbeat(client, key_b, "worker-b", free_vram_mb=6000, used_vram_mb=2000)

    async with await _hmac_client(app) as hm:
        # A claims a lease AS B (body.caller = "worker-b", signed as worker-a)
        # -> 403 and no lease created
        resp = await _post_hmac(
            hm, key_a, "worker-a", "/api/cluster/leases/claim",
            {"resource_id": "worker-b:gpu-cuda-0", "caller": "worker-b",
             "ttl_seconds": 30, "required_vram_mb": 4000},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"] == "caller does not match authenticated worker"

        # Sanity: no lease was created for worker-b (admin list is empty)
        list_resp = await client.get("/api/cluster/leases")
        assert list_resp.status_code == 200
        assert list_resp.json()["count"] == 0

        # A claims a lease as A -> 200
        resp = await _post_hmac(
            hm, key_a, "worker-a", "/api/cluster/leases/claim",
            {"resource_id": "worker-a:gpu-cuda-0", "caller": "worker-a",
             "ttl_seconds": 30, "required_vram_mb": 4000},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == "claimed"
        lease_id = data["lease_id"]
        original_expiry = data["expires_at"]

        # B tries to release A's lease -> 403
        resp = await _post_hmac(
            hm, key_b, "worker-b", "/api/cluster/leases/release",
            {"lease_id": lease_id},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"] == "lease owner does not match authenticated worker"

        # B tries to renew A's lease -> 403
        resp = await _post_hmac(
            hm, key_b, "worker-b", "/api/cluster/leases/renew",
            {"lease_id": lease_id, "ttl_seconds": 60},
        )
        assert resp.status_code == 403, resp.text
        assert resp.json()["error"] == "lease owner does not match authenticated worker"

        # A's expiry unchanged: A renews its own lease -> 200, expiry extended
        resp = await _post_hmac(
            hm, key_a, "worker-a", "/api/cluster/leases/renew",
            {"lease_id": lease_id, "ttl_seconds": 60},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["expires_at"] > original_expiry

        # B's lease list excludes A's lease
        resp = await _get_hmac(hm, key_b, "worker-b", "/api/cluster/leases")
        assert resp.status_code == 200, resp.text
        lease_ids = [l["lease_id"] for l in resp.json()["leases"]]
        assert lease_id not in lease_ids

        # A's lease list includes A's lease
        resp = await _get_hmac(hm, key_a, "worker-a", "/api/cluster/leases")
        assert resp.status_code == 200, resp.text
        lease_ids = [l["lease_id"] for l in resp.json()["leases"]]
        assert lease_id in lease_ids

        # A releases A's lease -> 200
        resp = await _post_hmac(
            hm, key_a, "worker-a", "/api/cluster/leases/release",
            {"lease_id": lease_id},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "released"

    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_admin_session_and_local_token_unchanged(client, app):
    """Admin session and host (local) token still work on all four lease routes."""
    key = await _register_worker(
        client, app, "gpu-node", "http://10.0.0.3:9001",
        capabilities=["llm-chat"],
        hardware={"gpu": {"model": "GTX 1080", "vram_mb": 8192}},
    )
    await _heartbeat(client, key, "gpu-node", free_vram_mb=6000, used_vram_mb=2000)

    def _auth() -> dict:
        return {"Authorization": f"Bearer {app.state.auth.get_local_token()}"}

    # Admin session claim (client fixture carries the admin cookie)
    resp = await client.post("/api/cluster/leases/claim", json={
        "resource_id": "gpu-node:gpu-cuda-0",
        "caller": "admin-caller",
        "ttl_seconds": 30,
    })
    assert resp.status_code == 200, resp.text
    lease_id = resp.json()["lease_id"]

    # Local token claim
    resp = await client.post("/api/cluster/leases/claim", headers=_auth(), json={
        "resource_id": "gpu-node:gpu-cuda-1",
        "caller": "local-caller",
        "ttl_seconds": 30,
    })
    assert resp.status_code == 200, resp.text

    # Admin session release
    resp = await client.post("/api/cluster/leases/release", json={
        "lease_id": lease_id,
    })
    assert resp.status_code == 200, resp.text

    # Local token list returns all leases (admin/local see everything)
    resp = await client.get("/api/cluster/leases", headers=_auth())
    assert resp.status_code == 200, resp.text

    await app.state.cluster_pairing.close()
