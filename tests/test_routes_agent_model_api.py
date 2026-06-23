"""Endpoint tests for the Agent-as-a-Model /v1 surface (decision 19)."""
import asyncio

import pytest


@pytest.fixture(autouse=True)
def _ensure_amk_store(client, tmp_path_factory):
    """Init app.state.agent_model_keys on a fresh DB; the test client registers
    the store but does not run the lifespan that init()s it (production does)."""
    store = client._transport.app.state.agent_model_keys
    if store._db is not None:
        try:
            asyncio.get_event_loop().run_until_complete(store.close())
        except Exception:
            pass
    tmp_dir = tmp_path_factory.mktemp("amk_test")
    store.db_path = tmp_dir / "agent_model_keys.db"
    asyncio.get_event_loop().run_until_complete(store.init())
    yield
    try:
        asyncio.get_event_loop().run_until_complete(store.close())
    except Exception:
        pass


@pytest.mark.asyncio
async def test_models_requires_a_key(client):
    resp = await client.get("/v1/models")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_models_rejects_unknown_key(client):
    resp = await client.get("/v1/models", headers={"Authorization": "Bearer sk-taosagent-nope"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_models_lists_consented_agents(client):
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a", "agent-b"], ["memory_read"])
    resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["object"] == "list"
    ids = [m["id"] for m in data["data"]]
    assert ids == ["agent-a", "agent-b"]
    assert all(m["object"] == "model" and m["owned_by"] == "taos-agent" for m in data["data"])


@pytest.mark.asyncio
async def test_models_revoked_key_is_rejected(client):
    store = client._transport.app.state.agent_model_keys
    token, rec = await store.mint("u1", ["agent-a"], [])
    await store.revoke(rec["id"])
    resp = await client.get("/v1/models", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
