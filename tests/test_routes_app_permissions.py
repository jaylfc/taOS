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
    await client.post(
        "/api/apps/a/permissions", json={"capability": "network", "decision": "denied"}
    )
    data = (await client.get("/api/apps/a/permissions")).json()
    assert "network" not in data["granted"]
    assert len(data["grants"]) == 1


@pytest.mark.asyncio
async def test_invalid_decision_returns_400(client):
    resp = await client.post(
        "/api/apps/a/permissions", json={"capability": "app.kv", "decision": "maybe"}
    )
    assert resp.status_code == 400


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
