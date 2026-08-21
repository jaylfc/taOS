"""Tests for cluster node revoke/block/unblock at the HTTP route layer.

Covers the same semantics as device revoke/block/unblock:
  - revoked node: HMAC auth rejected (401), can re-pair
  - blocked node: HMAC auth rejected (401), cannot re-pair until unblocked
  - unblock: allows re-pair; old signing key stays dead
  - revoke of node A does not affect node B
  - list_workers surfaces blocked/revoked/live_token
"""
from __future__ import annotations

import hashlib
import hmac
import json as _json
import time

import pytest

from test_routes_cluster_pairing import pair_worker, sign_worker_request


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


@pytest.mark.asyncio
class TestNodeRevokeRoute:
    """POST /api/cluster/workers/{name}/revoke -- admin only."""

    async def test_revoke_invalidates_hmac(self, client, app):
        """A revoked node's signing key must be rejected on the real
        device-bearer (worker HMAC) route -- heartbeat."""
        await app.state.cluster_pairing.init()
        key = await pair_worker(client, app, "revoke-node", "http://10.0.1.1:9000")

        # Worker can register and heartbeat before revoke.
        reg_body = _json.dumps({"name": "revoke-node", "url": "http://10.0.1.1:9000", "platform": "linux"}).encode()
        headers = sign_worker_request(key, "revoke-node", "POST", "/api/cluster/workers", reg_body)
        resp = await client.post("/api/cluster/workers", content=reg_body, headers={**headers, "content-type": "application/json"})
        assert resp.status_code == 200

        # Revoke.
        resp = await client.post("/api/cluster/workers/revoke-node/revoke")
        assert resp.status_code == 200
        assert resp.json() == {"revoked": True, "changed": True}

        # Heartbeat with the old key -> 401.
        hb_body = _json.dumps({"name": "revoke-node", "load": 0.1}).encode()
        headers = sign_worker_request(key, "revoke-node", "POST", "/api/cluster/heartbeat", hb_body)
        resp = await client.post("/api/cluster/heartbeat", content=hb_body, headers={**headers, "content-type": "application/json"})
        assert resp.status_code == 401

        await app.state.cluster_pairing.close()

    async def test_revoke_allows_repair(self, client, app):
        """A revoked node can re-pair via announce/confirm/claim."""
        await app.state.cluster_pairing.init()
        key = await pair_worker(client, app, "repaire-node", "http://10.0.2.1:9000")

        resp = await client.post("/api/cluster/workers/repaire-node/revoke")
        assert resp.status_code == 200
        assert await app.state.cluster_pairing.get_signing_key("repaire-node") is None

        # Re-pair with a new code.
        code2 = "repaire-code-2"
        ch = _code_hash(code2)
        resp = await client.post("/api/cluster/pairing/announce", json={"name": "repaire-node", "url": "http://10.0.2.1:9000", "platform": "linux", "code_hash": ch})
        assert resp.status_code == 200
        resp = await client.post("/api/cluster/pairing/confirm", json={"name": "repaire-node", "code": code2})
        assert resp.status_code == 200
        resp = await client.post("/api/cluster/pairing/claim", json={"name": "repaire-node", "code": code2})
        assert resp.status_code == 200
        new_key = bytes.fromhex(resp.json()["signing_key"])
        assert new_key != key
        assert await app.state.cluster_pairing.get_signing_key("repaire-node") is not None

        await app.state.cluster_pairing.close()

    async def test_revoke_unknown_worker_404(self, client, app):
        await app.state.cluster_pairing.init()
        resp = await client.post("/api/cluster/workers/no-such-node/revoke")
        assert resp.status_code == 404
        await app.state.cluster_pairing.close()

    async def test_revoke_idempotent(self, client, app):
        await app.state.cluster_pairing.init()
        await pair_worker(client, app, "idem-node", "http://10.0.3.1:9000")
        resp = await client.post("/api/cluster/workers/idem-node/revoke")
        assert resp.json()["changed"] is True
        resp = await client.post("/api/cluster/workers/idem-node/revoke")
        assert resp.json()["changed"] is False
        await app.state.cluster_pairing.close()


@pytest.mark.asyncio
class TestNodeBlockRoute:
    """POST /api/cluster/workers/{name}/block -- admin only."""

    async def test_block_invalidates_hmac(self, client, app):
        """A blocked node's signing key must be rejected on a real worker
        bearer route -- heartbeat."""
        await app.state.cluster_pairing.init()
        key = await pair_worker(client, app, "block-node", "http://10.0.4.1:9000")

        reg_body = _json.dumps({"name": "block-node", "url": "http://10.0.4.1:9000", "platform": "linux"}).encode()
        headers = sign_worker_request(key, "block-node", "POST", "/api/cluster/workers", reg_body)
        resp = await client.post("/api/cluster/workers", content=reg_body, headers={**headers, "content-type": "application/json"})
        assert resp.status_code == 200

        resp = await client.post("/api/cluster/workers/block-node/block")
        assert resp.status_code == 200
        assert resp.json() == {"blocked": True, "changed": True}

        hb_body = _json.dumps({"name": "block-node", "load": 0.1}).encode()
        headers = sign_worker_request(key, "block-node", "POST", "/api/cluster/heartbeat", hb_body)
        resp = await client.post("/api/cluster/heartbeat", content=hb_body, headers={**headers, "content-type": "application/json"})
        assert resp.status_code == 401

        await app.state.cluster_pairing.close()

    async def test_block_prevents_repair_until_unblocked(self, client, app):
        """A blocked node cannot re-pair -- confirm is refused while blocked."""
        await app.state.cluster_pairing.init()
        await pair_worker(client, app, "block-repair", "http://10.0.5.1:9000")

        resp = await client.post("/api/cluster/workers/block-repair/block")
        assert resp.status_code == 200

        # Re-announce with a new code.
        code2 = "block-repair-code-2"
        ch = _code_hash(code2)
        await client.post("/api/cluster/pairing/announce", json={"name": "block-repair", "url": "http://10.0.5.1:9000", "platform": "linux", "code_hash": ch})
        # Confirm must refuse (blocked).
        resp = await client.post("/api/cluster/pairing/confirm", json={"name": "block-repair", "code": code2})
        assert resp.status_code == 403, resp.text

        # Unblock -> confirm succeeds.
        resp = await client.post("/api/cluster/workers/block-repair/unblock")
        assert resp.status_code == 200
        assert resp.json() == {"unblocked": True, "changed": True}

        resp = await client.post("/api/cluster/pairing/confirm", json={"name": "block-repair", "code": code2})
        assert resp.status_code == 200
        state = await app.state.cluster_pairing.pairing_state("block-repair")
        assert state["blocked"] is False
        assert state["revoked"] is False

        await app.state.cluster_pairing.close()

    async def test_block_unknown_worker_404(self, client, app):
        await app.state.cluster_pairing.init()
        resp = await client.post("/api/cluster/workers/no-such-node/block")
        assert resp.status_code == 404
        await app.state.cluster_pairing.close()

    async def test_block_idempotent(self, client, app):
        await app.state.cluster_pairing.init()
        await pair_worker(client, app, "bidem-node", "http://10.0.6.1:9000")
        resp = await client.post("/api/cluster/workers/bidem-node/block")
        assert resp.json()["changed"] is True
        resp = await client.post("/api/cluster/workers/bidem-node/block")
        assert resp.json()["changed"] is False
        await app.state.cluster_pairing.close()


@pytest.mark.asyncio
class TestNodeUnblockRoute:
    """POST /api/cluster/workers/{name}/unblock -- admin only."""

    async def test_unblock_not_blocked_is_noop(self, client, app):
        await app.state.cluster_pairing.init()
        await pair_worker(client, app, "unblock-idle", "http://10.0.7.1:9000")
        resp = await client.post("/api/cluster/workers/unblock-idle/unblock")
        assert resp.status_code == 200
        assert resp.json() == {"unblocked": True, "changed": False}
        await app.state.cluster_pairing.close()

    async def test_unblock_unknown_worker_404(self, client, app):
        await app.state.cluster_pairing.init()
        resp = await client.post("/api/cluster/workers/no-such-node/unblock")
        assert resp.status_code == 404
        await app.state.cluster_pairing.close()


@pytest.mark.asyncio
class TestNodeRevokeIsolation:
    """Revoking node A must not affect node B."""

    async def test_revoke_isolates_nodes(self, client, app):
        await app.state.cluster_pairing.init()
        key_a = await pair_worker(client, app, "node-a", "http://10.0.8.1:9000")
        key_b = await pair_worker(client, app, "node-b", "http://10.0.8.2:9000")

        # Register both.
        for name, addr, key in [
            ("node-a", "http://10.0.8.1:9000", key_a),
            ("node-b", "http://10.0.8.2:9000", key_b),
        ]:
            reg_body = _json.dumps({"name": name, "url": addr, "platform": "linux"}).encode()
            headers = sign_worker_request(key, name, "POST", "/api/cluster/workers", reg_body)
            resp = await client.post("/api/cluster/workers", content=reg_body, headers={**headers, "content-type": "application/json"})
            assert resp.status_code == 200, resp.text

        # Revoke A.
        resp = await client.post("/api/cluster/workers/node-a/revoke")
        assert resp.status_code == 200

        # A can no longer heartbeat.
        hb_body = _json.dumps({"name": "node-a", "load": 0.1}).encode()
        headers = sign_worker_request(key_a, "node-a", "POST", "/api/cluster/heartbeat", hb_body)
        resp = await client.post("/api/cluster/heartbeat", content=hb_body, headers={**headers, "content-type": "application/json"})
        assert resp.status_code == 401

        # B is still fine.
        hb_body = _json.dumps({"name": "node-b", "load": 0.2}).encode()
        headers = sign_worker_request(key_b, "node-b", "POST", "/api/cluster/heartbeat", hb_body)
        resp = await client.post("/api/cluster/heartbeat", content=hb_body, headers={**headers, "content-type": "application/json"})
        assert resp.status_code == 200

        await app.state.cluster_pairing.close()


@pytest.mark.asyncio
class TestNodeListWorkers:
    """GET /api/cluster/workers surfaces blocked/revoked/live_token."""

    async def test_list_workers_surfaces_live_token(self, client, app):
        """An active node has live_token=true, revoked/blocked=false."""
        await app.state.cluster_pairing.init()
        await pair_worker(client, app, "live-node", "http://10.0.9.1:9000")

        reg_body = _json.dumps({"name": "live-node", "url": "http://10.0.9.1:9000", "platform": "linux"}).encode()
        key = await app.state.cluster_pairing.get_signing_key("live-node")
        assert key is not None
        headers = sign_worker_request(key, "live-node", "POST", "/api/cluster/workers", reg_body)
        resp = await client.post("/api/cluster/workers", content=reg_body, headers={**headers, "content-type": "application/json"})
        assert resp.status_code == 200

        workers = (await client.get("/api/cluster/workers")).json()
        me = next(w for w in workers if w["name"] == "live-node")
        assert me["live_token"] is True
        assert me["revoked"] is False
        assert me["blocked"] is False
        await app.state.cluster_pairing.close()

    async def test_list_workers_surfaces_revoked_state(self, client, app):
        await app.state.cluster_pairing.init()
        await pair_worker(client, app, "rev-node", "http://10.0.10.1:9000")
        key = await app.state.cluster_pairing.get_signing_key("rev-node")
        reg_body = _json.dumps({"name": "rev-node", "url": "http://10.0.10.1:9000", "platform": "linux"}).encode()
        headers = sign_worker_request(key, "rev-node", "POST", "/api/cluster/workers", reg_body)
        resp = await client.post("/api/cluster/workers", content=reg_body, headers={**headers, "content-type": "application/json"})
        assert resp.status_code == 200

        resp = await client.post("/api/cluster/workers/rev-node/revoke")
        assert resp.status_code == 200

        workers = (await client.get("/api/cluster/workers")).json()
        me = next(w for w in workers if w["name"] == "rev-node")
        assert me["live_token"] is False
        assert me["revoked"] is True
        assert me["blocked"] is False
        await app.state.cluster_pairing.close()

    async def test_list_workers_surfaces_blocked_state(self, client, app):
        await app.state.cluster_pairing.init()
        await pair_worker(client, app, "blk-node", "http://10.0.11.1:9000")
        key = await app.state.cluster_pairing.get_signing_key("blk-node")
        reg_body = _json.dumps({"name": "blk-node", "url": "http://10.0.11.1:9000", "platform": "linux"}).encode()
        headers = sign_worker_request(key, "blk-node", "POST", "/api/cluster/workers", reg_body)
        resp = await client.post("/api/cluster/workers", content=reg_body, headers={**headers, "content-type": "application/json"})
        assert resp.status_code == 200

        resp = await client.post("/api/cluster/workers/blk-node/block")
        assert resp.status_code == 200

        workers = (await client.get("/api/cluster/workers")).json()
        me = next(w for w in workers if w["name"] == "blk-node")
        assert me["live_token"] is False
        assert me["revoked"] is True
        assert me["blocked"] is True
        await app.state.cluster_pairing.close()
