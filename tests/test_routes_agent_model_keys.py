"""Endpoint tests for routes/agent_model_keys.py (decision 19, owner surface)."""
import asyncio

import pytest


@pytest.fixture(autouse=True)
def _ensure_agent_model_key_store(client, tmp_path_factory):
    """Init app.state.agent_model_keys on a fresh DB; the test client registers
    the store but does not run the lifespan that init()s it (production does)."""
    store = client._transport.app.state.agent_model_keys
    if store._db is not None:
        try:
            asyncio.get_event_loop().run_until_complete(store.close())
        except Exception:
            pass
    tmp_dir = tmp_path_factory.mktemp("agent_model_keys_test")
    store.db_path = tmp_dir / "agent_model_keys.db"
    asyncio.get_event_loop().run_until_complete(store.init())
    yield
    try:
        asyncio.get_event_loop().run_until_complete(store.close())
    except Exception:
        pass


@pytest.mark.asyncio
async def test_list_empty_by_default(client):
    resp = await client.get("/api/agent-model-keys")
    assert resp.status_code == 200
    assert resp.json()["keys"] == []


@pytest.mark.asyncio
async def test_mint_returns_token_once_then_listed(client):
    resp = await client.post(
        "/api/agent-model-keys",
        json={"agent_ids": ["assistant-20260101-000000"], "scopes": ["chat"]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["key"].startswith("sk-taosagent-")
    assert body["record"]["agent_ids"] == ["assistant-20260101-000000"]
    assert "key_hash" not in body["record"]  # never surface the hash

    listed = (await client.get("/api/agent-model-keys")).json()["keys"]
    assert len(listed) == 1
    assert "key" not in listed[0]  # the plaintext is never re-served


@pytest.mark.asyncio
async def test_mint_without_agent_ids_returns_400(client):
    resp = await client.post(
        "/api/agent-model-keys", json={"agent_ids": [], "scopes": []}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_mint_naive_expiry_returns_400(client):
    resp = await client.post(
        "/api/agent-model-keys",
        json={"agent_ids": ["a"], "expires_at": "2030-01-01T00:00:00"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_revoke_own_key(client):
    rec = (
        await client.post("/api/agent-model-keys", json={"agent_ids": ["a"]})
    ).json()["record"]
    resp = await client.post(f"/api/agent-model-keys/{rec['id']}/revoke")
    assert resp.status_code == 200
    listed = (await client.get("/api/agent-model-keys")).json()["keys"]
    assert listed[0]["revoked"] is True


@pytest.mark.asyncio
async def test_revoke_unknown_key_returns_404(client):
    resp = await client.post("/api/agent-model-keys/999999/revoke")
    assert resp.status_code == 404
