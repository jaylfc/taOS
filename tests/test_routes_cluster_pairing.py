"""Tests for the cluster pairing flow and HMAC gate on worker endpoints.

Coverage:
- announce -> pending visible to admin
- confirm right/wrong code, expiry, attempt cap invalidation
- claim before confirm (202), after confirm (key once, second claim 404)
- full happy path announce -> confirm -> claim -> HMAC register -> heartbeat
- unsigned register/heartbeat -> 401 worker_not_paired
- bad signature, stale timestamp, header/body name mismatch
- GET /api/cluster/workers still public
- pairing pending/confirm require a session
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def sign_worker_request(
    key: bytes,
    name: str,
    method: str,
    path: str,
    body: bytes,
) -> dict:
    """Return the three HMAC auth headers for a worker request."""
    ts = str(int(time.time()))
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{ts}.{method.upper()}.{path}.{body_hash}".encode()
    sig = hmac.new(key, message, hashlib.sha256).hexdigest()
    return {
        "X-TAOS-Worker-Name": name,
        "X-TAOS-Timestamp": ts,
        "X-TAOS-Signature": sig,
    }


async def pair_worker(
    client: AsyncClient,
    app,
    name: str,
    url: str,
    platform: str = "linux",
    code: str = "test-code-123",
) -> bytes:
    """Drive the full pairing flow and return the signing key."""
    ch = _code_hash(code)
    resp = await client.post(
        "/api/cluster/pairing/announce",
        json={"name": name, "url": url, "platform": platform, "code_hash": ch},
    )
    assert resp.status_code == 200, resp.text

    resp = await client.post(
        "/api/cluster/pairing/confirm",
        json={"name": name, "code": code},
    )
    assert resp.status_code == 200, resp.text

    resp = await client.post(
        "/api/cluster/pairing/claim",
        json={"name": name, "code": code},
    )
    assert resp.status_code == 200, resp.text
    return bytes.fromhex(resp.json()["signing_key"])


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def unauthed_client(app, tmp_data_dir):
    """Client with no session cookie (anonymous)."""
    store = app.state.cluster_pairing
    await store.init()
    app.state._startup_complete = True
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
    ) as c:
        yield c
    await store.close()


# ---------------------------------------------------------------------------
# announce
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_announce_returns_pending(client, app):
    """A valid announce creates a pending entry visible to admin."""
    await app.state.cluster_pairing.init()
    code = "secret-code"
    resp = await client.post(
        "/api/cluster/pairing/announce",
        json={
            "name": "worker-a",
            "url": "http://10.0.0.1:9000",
            "platform": "linux",
            "code_hash": _code_hash(code),
        },
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    resp = await client.get("/api/cluster/pairing/pending")
    assert resp.status_code == 200
    items = resp.json()
    assert any(w["name"] == "worker-a" for w in items)
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_announce_rejects_invalid_code_hash(client, app):
    """code_hash that is not 64 lowercase hex chars -> 400."""
    await app.state.cluster_pairing.init()
    resp = await client.post(
        "/api/cluster/pairing/announce",
        json={"name": "w1", "url": "http://10.0.0.1:9000", "code_hash": "tooshort"},
    )
    assert resp.status_code == 400
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_announce_rejects_empty_name(client, app):
    """Empty name -> 400."""
    await app.state.cluster_pairing.init()
    resp = await client.post(
        "/api/cluster/pairing/announce",
        json={"name": "", "url": "http://10.0.0.1:9000", "code_hash": _code_hash("x")},
    )
    assert resp.status_code == 400
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_announce_rejects_empty_url(client, app):
    """Empty url -> 400."""
    await app.state.cluster_pairing.init()
    resp = await client.post(
        "/api/cluster/pairing/announce",
        json={"name": "w1", "url": "", "code_hash": _code_hash("x")},
    )
    assert resp.status_code == 400
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_announce_no_auth_required(app, tmp_data_dir):
    """announce is unauthenticated — no session cookie needed."""
    await app.state.cluster_pairing.init()
    transport = ASGITransport(app=app)
    app.state._startup_complete = True
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post(
            "/api/cluster/pairing/announce",
            json={"name": "anon-worker", "url": "http://10.0.0.2:9000", "code_hash": _code_hash("c")},
        )
    # 200 or 400 acceptable (auth must not be the reason for failure)
    assert resp.status_code != 401
    await app.state.cluster_pairing.close()


# ---------------------------------------------------------------------------
# pending (admin-only)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pending_requires_session(app, tmp_data_dir):
    """GET /api/cluster/pairing/pending without a session -> 401 or redirect."""
    await app.state.cluster_pairing.init()
    transport = ASGITransport(app=app)
    app.state._startup_complete = True
    app.state.auth.setup_user("admin", "Admin", "", "testpass123")
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/cluster/pairing/pending")
    assert resp.status_code in (401, 403)
    await app.state.cluster_pairing.close()


# ---------------------------------------------------------------------------
# confirm
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_confirm_right_code(client, app):
    """Admin confirms with correct code -> 200 confirmed."""
    await app.state.cluster_pairing.init()
    code = "right-code"
    await client.post(
        "/api/cluster/pairing/announce",
        json={"name": "w2", "url": "http://10.0.0.3:9000", "code_hash": _code_hash(code)},
    )
    resp = await client.post("/api/cluster/pairing/confirm", json={"name": "w2", "code": code})
    assert resp.status_code == 200
    assert resp.json()["status"] == "confirmed"
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_confirm_wrong_code(client, app):
    """Wrong code -> 403."""
    await app.state.cluster_pairing.init()
    await client.post(
        "/api/cluster/pairing/announce",
        json={"name": "w3", "url": "http://10.0.0.4:9000", "code_hash": _code_hash("real-code")},
    )
    resp = await client.post("/api/cluster/pairing/confirm", json={"name": "w3", "code": "wrong-code"})
    assert resp.status_code == 403
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_confirm_unknown_worker(client, app):
    """No pending entry for name -> 404."""
    await app.state.cluster_pairing.init()
    resp = await client.post("/api/cluster/pairing/confirm", json={"name": "nobody", "code": "x"})
    assert resp.status_code == 404
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_confirm_expired_entry(client, app, monkeypatch):
    """Entry older than 15 min -> 410 Gone."""
    await app.state.cluster_pairing.init()
    code = "exp-code"
    await client.post(
        "/api/cluster/pairing/announce",
        json={"name": "w-exp", "url": "http://10.0.0.5:9000", "code_hash": _code_hash(code)},
    )
    # Monkeypatch time so the entry appears expired
    import tinyagentos.cluster.pairing_store as _ps
    monkeypatch.setattr(_ps, "_now", lambda: time.time() + 1000)
    resp = await client.post("/api/cluster/pairing/confirm", json={"name": "w-exp", "code": code})
    assert resp.status_code == 410
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_confirm_attempt_cap_invalidates(client, app):
    """5 wrong codes invalidate the pending entry -> further confirm returns 404."""
    await app.state.cluster_pairing.init()
    await client.post(
        "/api/cluster/pairing/announce",
        json={"name": "w-cap", "url": "http://10.0.0.6:9000", "code_hash": _code_hash("real")},
    )
    for _ in range(5):
        await client.post("/api/cluster/pairing/confirm", json={"name": "w-cap", "code": "wrong"})
    # 5 attempts exhausted — next call should 404 (invalidated)
    resp = await client.post("/api/cluster/pairing/confirm", json={"name": "w-cap", "code": "real"})
    assert resp.status_code == 404
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_confirm_requires_session(app, tmp_data_dir):
    """confirm without a session -> 401."""
    await app.state.cluster_pairing.init()
    transport = ASGITransport(app=app)
    app.state._startup_complete = True
    app.state.auth.setup_user("admin", "Admin", "", "testpass123")
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/cluster/pairing/confirm", json={"name": "w", "code": "c"})
    assert resp.status_code in (401, 403)
    await app.state.cluster_pairing.close()


# ---------------------------------------------------------------------------
# claim
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_claim_before_confirm_returns_202(client, app):
    """Claim before admin has confirmed -> 202 awaiting_confirm."""
    await app.state.cluster_pairing.init()
    code = "claim-code"
    await client.post(
        "/api/cluster/pairing/announce",
        json={"name": "w-claim", "url": "http://10.0.0.7:9000", "code_hash": _code_hash(code)},
    )
    resp = await client.post("/api/cluster/pairing/claim", json={"name": "w-claim", "code": code})
    assert resp.status_code == 202
    assert resp.json()["status"] == "awaiting_confirm"
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_claim_after_confirm_delivers_key_once(client, app):
    """After confirm the key is delivered exactly once; second claim -> 404."""
    await app.state.cluster_pairing.init()
    code = "once-code"
    await client.post(
        "/api/cluster/pairing/announce",
        json={"name": "w-once", "url": "http://10.0.0.8:9000", "code_hash": _code_hash(code)},
    )
    await client.post("/api/cluster/pairing/confirm", json={"name": "w-once", "code": code})

    # First claim delivers key
    resp = await client.post("/api/cluster/pairing/claim", json={"name": "w-once", "code": code})
    assert resp.status_code == 200
    key_hex = resp.json()["signing_key"]
    assert len(bytes.fromhex(key_hex)) == 32

    # Second claim -> 404 (pending fields cleared)
    resp2 = await client.post("/api/cluster/pairing/claim", json={"name": "w-once", "code": code})
    assert resp2.status_code == 404
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_claim_wrong_code_after_confirm(client, app):
    """After confirm, wrong code on claim -> 403."""
    await app.state.cluster_pairing.init()
    code = "right"
    await client.post(
        "/api/cluster/pairing/announce",
        json={"name": "w-wrong", "url": "http://10.0.0.9:9000", "code_hash": _code_hash(code)},
    )
    await client.post("/api/cluster/pairing/confirm", json={"name": "w-wrong", "code": code})
    resp = await client.post("/api/cluster/pairing/claim", json={"name": "w-wrong", "code": "bad"})
    assert resp.status_code == 403
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_claim_unknown_worker(client, app):
    """claim for unknown name -> 404."""
    await app.state.cluster_pairing.init()
    resp = await client.post("/api/cluster/pairing/claim", json={"name": "ghost", "code": "x"})
    assert resp.status_code == 404
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_claim_no_auth_required(app, tmp_data_dir):
    """claim is unauthenticated."""
    await app.state.cluster_pairing.init()
    transport = ASGITransport(app=app)
    app.state._startup_complete = True
    app.state.auth.setup_user("admin", "Admin", "", "testpass123")
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.post("/api/cluster/pairing/claim", json={"name": "nobody", "code": "c"})
    # 404 for unknown, but NOT a 401
    assert resp.status_code != 401
    await app.state.cluster_pairing.close()


# ---------------------------------------------------------------------------
# Full happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_full_pairing_flow_then_hmac_register_and_heartbeat(client, app):
    """announce -> confirm -> claim -> HMAC-signed register -> signed heartbeat."""
    await app.state.cluster_pairing.init()

    key = await pair_worker(client, app, "full-worker", "http://10.1.0.1:9000")

    # Signed register
    import json as _json
    reg_body = _json.dumps({
        "name": "full-worker",
        "url": "http://10.1.0.1:9000",
        "platform": "linux",
    }).encode()
    headers = sign_worker_request(key, "full-worker", "POST", "/api/cluster/workers", reg_body)
    resp = await client.post(
        "/api/cluster/workers",
        content=reg_body,
        headers={**headers, "content-type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "registered"

    # Signed heartbeat
    hb_body = _json.dumps({"name": "full-worker", "load": 0.1}).encode()
    headers = sign_worker_request(key, "full-worker", "POST", "/api/cluster/heartbeat", hb_body)
    resp = await client.post(
        "/api/cluster/heartbeat",
        content=hb_body,
        headers={**headers, "content-type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"

    await app.state.cluster_pairing.close()


# ---------------------------------------------------------------------------
# HMAC gate: unsigned requests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unsigned_register_returns_401_worker_not_paired(client, app):
    """POST /api/cluster/workers without HMAC headers -> 401 worker_not_paired."""
    await app.state.cluster_pairing.init()
    resp = await client.post(
        "/api/cluster/workers",
        json={"name": "unsigned-worker", "url": "http://10.0.0.10:9000"},
    )
    assert resp.status_code == 401
    data = resp.json()
    assert data.get("code") == "worker_not_paired"
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_unsigned_heartbeat_returns_401_worker_not_paired(client, app):
    """POST /api/cluster/heartbeat without HMAC headers -> 401 worker_not_paired."""
    await app.state.cluster_pairing.init()
    resp = await client.post(
        "/api/cluster/heartbeat",
        json={"name": "unsigned-worker"},
    )
    assert resp.status_code == 401
    data = resp.json()
    assert data.get("code") == "worker_not_paired"
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_bad_signature_returns_401(client, app):
    """Correct headers but wrong signature -> 401 bad_signature."""
    await app.state.cluster_pairing.init()
    _key = await pair_worker(client, app, "sig-worker", "http://10.1.0.2:9000")

    import json as _json
    reg_body = _json.dumps({"name": "sig-worker", "url": "http://10.1.0.2:9000"}).encode()
    ts = str(int(time.time()))
    resp = await client.post(
        "/api/cluster/workers",
        content=reg_body,
        headers={
            "X-TAOS-Worker-Name": "sig-worker",
            "X-TAOS-Timestamp": ts,
            "X-TAOS-Signature": "a" * 64,
            "content-type": "application/json",
        },
    )
    assert resp.status_code == 401
    assert resp.json().get("code") == "bad_signature"
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_stale_timestamp_returns_401(client, app):
    """Timestamp more than 300s old -> 401 stale_timestamp."""
    await app.state.cluster_pairing.init()
    key = await pair_worker(client, app, "ts-worker", "http://10.1.0.3:9000")

    import json as _json
    reg_body = _json.dumps({"name": "ts-worker", "url": "http://10.1.0.3:9000"}).encode()
    stale_ts = str(int(time.time()) - 400)
    body_hash = hashlib.sha256(reg_body).hexdigest()
    message = f"{stale_ts}.POST./api/cluster/workers.{body_hash}".encode()
    sig = hmac.new(key, message, hashlib.sha256).hexdigest()
    resp = await client.post(
        "/api/cluster/workers",
        content=reg_body,
        headers={
            "X-TAOS-Worker-Name": "ts-worker",
            "X-TAOS-Timestamp": stale_ts,
            "X-TAOS-Signature": sig,
            "content-type": "application/json",
        },
    )
    assert resp.status_code == 401
    assert resp.json().get("code") == "stale_timestamp"
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_unknown_worker_name_in_header_returns_401(client, app):
    """HMAC headers present but worker name not paired -> 401 worker_not_paired."""
    await app.state.cluster_pairing.init()
    import json as _json
    reg_body = _json.dumps({"name": "unknown-ghost", "url": "http://10.1.0.4:9000"}).encode()
    ts = str(int(time.time()))
    fake_key = secrets.token_bytes(32)
    body_hash = hashlib.sha256(reg_body).hexdigest()
    message = f"{ts}.POST./api/cluster/workers.{body_hash}".encode()
    sig = hmac.new(fake_key, message, hashlib.sha256).hexdigest()
    resp = await client.post(
        "/api/cluster/workers",
        content=reg_body,
        headers={
            "X-TAOS-Worker-Name": "unknown-ghost",
            "X-TAOS-Timestamp": ts,
            "X-TAOS-Signature": sig,
            "content-type": "application/json",
        },
    )
    assert resp.status_code == 401
    assert resp.json().get("code") == "worker_not_paired"
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_header_body_name_mismatch_returns_403(client, app):
    """Header worker name != body name -> 403."""
    await app.state.cluster_pairing.init()
    key_a = await pair_worker(client, app, "worker-a2", "http://10.2.0.1:9000")

    import json as _json
    # Sign as worker-a2 but body says worker-b
    reg_body = _json.dumps({"name": "worker-b", "url": "http://10.2.0.2:9000"}).encode()
    headers = sign_worker_request(key_a, "worker-a2", "POST", "/api/cluster/workers", reg_body)
    resp = await client.post(
        "/api/cluster/workers",
        content=reg_body,
        headers={**headers, "content-type": "application/json"},
    )
    assert resp.status_code == 403
    await app.state.cluster_pairing.close()


# ---------------------------------------------------------------------------
# GET /api/cluster/workers still public
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_workers_still_public(app, tmp_data_dir):
    """GET /api/cluster/workers must be accessible without any auth."""
    await app.state.cluster_pairing.init()
    transport = ASGITransport(app=app)
    app.state._startup_complete = True
    app.state.auth.setup_user("admin", "Admin", "", "testpass123")
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        resp = await c.get("/api/cluster/workers")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    await app.state.cluster_pairing.close()
