"""Issue #3227 -- credential-aware worker list on GET /api/cluster/workers.

``GET /api/cluster/workers`` is on the auth-exempt list, so the middleware
never consults a bearer there; the route used to read only the session cookie
and hand every other caller the stripped ``{name, status, tier_id, kind}``
projection.  Headless callers (a local-token dispatcher, or an agent tool
holding a registry JWT) therefore could not see ``capabilities``, ``backends``,
``resources``, ``free_vram_mb`` or ``models``/``loaded_models``.

The fix (mix of the issue's options (a) and (c)) lives in the ROUTE only, so
the exempt gate still ignores bearers on every other path:

  * valid local token (``validate_local_token``)   -> full ADMIN view;
  * registry JWT with an active ``cluster_read`` grant -> "full but not admin"
    view (worker detail minus pairing state, ``host_lan_ip`` and ``hardware``);
  * no credential / invalid token / JWT without the scope -> the anonymous
    stripped view, byte-identical to before.

``signing_key`` is stripped in every branch.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token

_STRIPPED_KEYS = {"name", "status", "tier_id", "kind"}
_PAIRING_STATE = {"revoked", "blocked", "live_token"}
_HOST_FIELDS = {"host_lan_ip", "hardware"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def _sign(key: bytes, name: str, method: str, path: str, body: bytes) -> dict:
    ts = str(int(time.time()))
    body_hash = hashlib.sha256(body).hexdigest()
    message = f"{ts}.{method.upper()}.{path}.{body_hash}".encode()
    return {
        "X-TAOS-Worker-Name": name,
        "X-TAOS-Timestamp": ts,
        "X-TAOS-Signature": hmac.new(key, message, hashlib.sha256).hexdigest(),
    }


async def _register_worker(client, app, name: str, url: str, host_lan_ip: str) -> bytes:
    """Pair + register a worker carrying the full detail set, return its key."""
    await app.state.cluster_pairing.init()
    code = "test-pairing-code"
    r = await client.post(
        "/api/cluster/pairing/announce",
        json={"name": name, "url": url, "platform": "linux", "code_hash": _code_hash(code)},
    )
    assert r.status_code == 200, r.text
    for step in ("confirm", "claim"):
        r = await client.post(f"/api/cluster/pairing/{step}", json={"name": name, "code": code})
        assert r.status_code == 200, r.text
    key = bytes.fromhex(r.json()["signing_key"])

    body = json.dumps({
        "name": name,
        "url": url,
        "platform": "linux",
        "capabilities": ["chat", "embed"],
        "hardware": {
            "cpu": {"model": "Ryzen 9", "arch": "x86_64"},
            "ram_gb": 64,
            "os": {"kernel": "6.1.0", "distro": "ubuntu", "version": "24.04"},
        },
        "models": ["llama3"],
        "resources": ["gpu-cuda-0", "cpu-inference"],
        "host_lan_ip": host_lan_ip,
        "worker_url": f"http://{name}:9000",
        "backends": [{"name": "llama-cpp", "models": [{"name": "llama3"}], "loaded_models": ["llama3"]}],
    }).encode()
    r = await client.post(
        "/api/cluster/workers",
        content=body,
        headers={**_sign(key, name, "POST", "/api/cluster/workers", body), "content-type": "application/json"},
    )
    assert r.status_code == 200, r.text

    # free_vram_mb arrives on a heartbeat (not in the register body).
    hb = json.dumps({"name": name, "free_vram_mb": 8192, "used_vram_mb": 2048}).encode()
    r = await client.post(
        "/api/cluster/heartbeat",
        content=hb,
        headers={**_sign(key, name, "POST", "/api/cluster/heartbeat", hb), "content-type": "application/json"},
    )
    assert r.status_code == 200, r.text
    return key


def _bearer_client(app, token: str) -> AsyncClient:
    """Cookieless client authenticating only via ``Authorization: Bearer``."""
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _mint_agent(app, scopes, handle="@taOS-cluster-read"):
    """Register an active agent, grant *scopes* globally, return a bearer JWT."""
    registry = app.state.agent_registry
    grants = app.state.agent_grants
    for store in (registry, grants):
        if store._db is None:
            await store.init()
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="claude-code",
        display_name="taOS cluster reader",
        allow_reserved=True,
        origin="internal",
        handle=handle,
    )
    cid = rec["canonical_id"]
    if rec.get("status") != "active":
        await registry.set_status(cid, "active")
    for scope in scopes:
        await grants.add_grant(cid, scope, project_id=None)
    token = mint_registry_token(
        cid, priv, user_id="u", framework="claude-code", project_id=None
    )
    return cid, token


def _only(workers):
    assert len(workers) == 1, workers
    return workers[0]


# ---------------------------------------------------------------------------
# valid local token -> full admin view
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_local_bearer_gets_full_admin_view(client, app):
    """A valid local token earns the full ADMIN view, not the reduced one."""
    await _register_worker(client, app, "local-tok-worker", "http://10.0.0.9:9000", "192.168.1.90")
    token = app.state.auth.get_local_token()

    async with _bearer_client(app, token) as ac:
        r = await ac.get("/api/cluster/workers")
    assert r.status_code == 200, r.text
    w = _only(r.json())

    assert w["name"] == "local-tok-worker"
    # The full detail the headless caller needs.
    for f in ("url", "capabilities", "backends", "resources", "free_vram_mb", "models"):
        assert f in w, f"missing {f}"
    assert w["free_vram_mb"] == 8192
    # Admin view also carries pairing state and host fields...
    for f in _PAIRING_STATE | _HOST_FIELDS:
        assert f in w, f"missing {f}"
    # ...but never the signing key.
    assert "signing_key" not in w
    await app.state.cluster_pairing.close()


# ---------------------------------------------------------------------------
# registry JWT with cluster_read -> "full but not admin"
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cluster_read_jwt_gets_full_not_admin_view(client, app):
    """A registry JWT holding cluster_read gets worker detail minus admin-only
    and host-identifying fields."""
    await _register_worker(client, app, "cr-worker", "http://10.0.0.10:9000", "192.168.1.91")
    _cid, token = await _mint_agent(app, ["cluster_read"])

    async with _bearer_client(app, token) as ac:
        r = await ac.get("/api/cluster/workers")
    assert r.status_code == 200, r.text
    w = _only(r.json())

    assert w["name"] == "cr-worker"
    # "full": the integration-visible detail must be present.
    for f in ("capabilities", "backends", "resources", "free_vram_mb", "models", "tier_id"):
        assert f in w, f"missing {f}"
    assert w["free_vram_mb"] == 8192
    # "not admin": pairing state and host-identifying fields are withheld.
    for f in _PAIRING_STATE | _HOST_FIELDS | {"signing_key"}:
        assert f not in w, f"{f} must not leak in the cluster_read view"
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_cluster_read_view_has_no_pairing_state_even_when_revoked(client, app):
    """The cluster_read view must not reveal revoke/block state."""
    await _register_worker(client, app, "cr-revoked", "http://10.0.0.11:9000", "192.168.1.92")
    _cid, token = await _mint_agent(app, ["cluster_read"])

    async with _bearer_client(app, token) as ac:
        r = await ac.get("/api/cluster/workers")
    assert r.status_code == 200, r.text
    w = _only(r.json())
    assert _PAIRING_STATE.isdisjoint(w)
    await app.state.cluster_pairing.close()


# ---------------------------------------------------------------------------
# fall-through: anonymous view stays anonymous
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_bearer_gets_anonymous_view(client, app):
    """An invalid bearer falls through to anonymous -- NOT a 401."""
    await _register_worker(client, app, "anon-worker", "http://10.0.0.12:9000", "192.168.1.93")

    async with _bearer_client(app, "definitely-not-a-real-token") as ac:
        r = await ac.get("/api/cluster/workers")
    assert r.status_code == 200, r.text
    w = _only(r.json())
    assert set(w) == _STRIPPED_KEYS
    await app.state.cluster_pairing.close()


@pytest.mark.asyncio
async def test_jwt_without_cluster_read_scope_gets_anonymous_view(client, app):
    """A valid registry JWT WITHOUT cluster_read gets the anonymous view."""
    await _register_worker(client, app, "noscope-worker", "http://10.0.0.13:9000", "192.168.1.94")
    _cid, token = await _mint_agent(app, ["registry_feeds_read"])

    async with _bearer_client(app, token) as ac:
        r = await ac.get("/api/cluster/workers")
    assert r.status_code == 200, r.text
    w = _only(r.json())
    assert set(w) == _STRIPPED_KEYS
    await app.state.cluster_pairing.close()
