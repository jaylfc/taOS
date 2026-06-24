"""Endpoint tests for routes/app_permissions.py (#56)."""
import asyncio

import pytest


@pytest.fixture(autouse=True)
def _ensure_app_grants_store(client, tmp_path_factory):
    """Init app.state.app_grants on a fresh DB; the test client registers the
    store but does not run the lifespan that init()s it (production does)."""
    store = client._transport.app.state.app_grants
    if store._db is not None:
        try:
            asyncio.get_event_loop().run_until_complete(store.close())
        except Exception:
            pass
    tmp_dir = tmp_path_factory.mktemp("app_grants_test")
    store.db_path = tmp_dir / "app_grants.db"
    asyncio.get_event_loop().run_until_complete(store.init())
    yield
    try:
        asyncio.get_event_loop().run_until_complete(store.close())
    except Exception:
        pass


@pytest.mark.asyncio
async def test_permissions_empty_by_default(client):
    resp = await client.get("/api/apps/stream-chat/permissions")
    assert resp.status_code == 200
    data = resp.json()
    assert data["app_id"] == "stream-chat"
    assert data["grants"] == []
    assert data["granted"] == []


@pytest.mark.asyncio
async def test_grant_then_listed_as_granted(client):
    resp = await client.post(
        "/api/apps/stream-chat/permissions",
        json={"capability": "app.kv", "decision": "granted"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["grant"]["capability"] == "app.kv"
    data = (await client.get("/api/apps/stream-chat/permissions")).json()
    assert data["granted"] == ["app.kv"]


@pytest.mark.asyncio
async def test_denied_capability_not_in_granted(client):
    cap = "network:https://example.com"
    await client.post(
        "/api/apps/a/permissions", json={"capability": cap, "decision": "denied"}
    )
    data = (await client.get("/api/apps/a/permissions")).json()
    assert cap not in data["granted"]
    assert len(data["grants"]) == 1


@pytest.mark.asyncio
async def test_invalid_decision_returns_400(client):
    resp = await client.post(
        "/api/apps/a/permissions", json={"capability": "app.kv", "decision": "maybe"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_grant_unknown_capability_returns_400(client):
    # The grant API now validates against the closed capability vocabulary, so a
    # typo'd or made-up capability cannot be recorded in the ledger.
    resp = await client.post(
        "/api/apps/a/permissions",
        json={"capability": "files.read", "decision": "granted"},
    )
    assert resp.status_code == 400
    assert "unknown capability" in resp.json()["error"]
    # Nothing was recorded.
    data = (await client.get("/api/apps/a/permissions")).json()
    assert data["grants"] == []


@pytest.mark.asyncio
async def test_grant_network_origin_capability_allowed(client):
    # The parametrized network:<origin> form is a known capability.
    resp = await client.post(
        "/api/apps/a/permissions",
        json={"capability": "network:https://api.example.com", "decision": "granted"},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["network:", "network:not an origin", "network:ftp://x.com"])
async def test_grant_malformed_network_origin_returns_400(client, bad):
    # The bare network: prefix passes is_known_capability, but a grant must carry
    # a well-formed origin, so a malformed one is rejected and not recorded.
    resp = await client.post(
        "/api/apps/a/permissions", json={"capability": bad, "decision": "granted"}
    )
    assert resp.status_code == 400
    assert "invalid network origin" in resp.json()["error"]
    data = (await client.get("/api/apps/a/permissions")).json()
    assert data["grants"] == []


@pytest.mark.asyncio
async def test_empty_capability_returns_400(client):
    resp = await client.post(
        "/api/apps/a/permissions", json={"capability": "  ", "decision": "granted"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_revoke_removes_from_granted(client):
    await client.post(
        "/api/apps/a/permissions", json={"capability": "files.write", "decision": "granted"}
    )
    resp = await client.post(
        "/api/apps/a/permissions/revoke", json={"capability": "files.write"}
    )
    assert resp.status_code == 200
    data = (await client.get("/api/apps/a/permissions")).json()
    assert "files.write" not in data["granted"]


@pytest.mark.asyncio
async def test_request_consent_creates_decision_for_gated_caps(client):
    resp = await client.post(
        "/api/apps/stream-chat/request-consent",
        json={"capabilities": ["app.kv", "app.net", "app.memory"]},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # Free caps (app.kv) are auto-granted, so only the gated caps need consent.
    assert data["pending"] == ["app.net", "app.memory"]
    dec = data["decision"]
    assert dec["type"] == "multi_select"
    assert dec["metadata"] == {
        "kind": "app_grant", "app_id": "stream-chat",
        "capabilities": ["app.net", "app.memory"],
    }
    # Answering the consent Decision writes the grants (the #1429 side-effect).
    resp = await client.post(f"/api/decisions/{dec['id']}/answer", json={"value": ["app.net"]})
    assert resp.status_code == 200
    data = (await client.get("/api/apps/stream-chat/permissions")).json()
    assert data["granted"] == ["app.net"]


@pytest.mark.asyncio
async def test_request_consent_noop_when_all_free_or_decided(client):
    # All free caps: nothing to consent to.
    resp = await client.post("/api/apps/a/request-consent", json={"capabilities": ["app.kv"]})
    assert resp.json() == {"decision": None, "pending": []}
    # Already-decided gated caps are not re-prompted.
    await client.post("/api/apps/a/permissions", json={"capability": "app.net", "decision": "denied"})
    resp = await client.post("/api/apps/a/request-consent", json={"capabilities": ["app.net"]})
    assert resp.json() == {"decision": None, "pending": []}


@pytest.mark.asyncio
async def test_request_consent_rejects_unknown_capability(client):
    resp = await client.post("/api/apps/a/request-consent", json={"capabilities": ["app.bogus"]})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_request_consent_dedups_capabilities(client):
    # A repeated capability must not create a colliding option (whose value the
    # dedup suffixer would rewrite to a non-capability like "app.net (2)").
    resp = await client.post(
        "/api/apps/dup-app/request-consent",
        json={"capabilities": ["app.net", "app.net", "app.memory"]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["pending"] == ["app.net", "app.memory"]
    values = [o["value"] for o in data["decision"]["options"]]
    assert values == ["app.net", "app.memory"]
