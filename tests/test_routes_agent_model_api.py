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


# --- POST /v1/chat/completions: the consent contract (turn execution pending) ---


def _chat_body(model="agent-a"):
    return {"model": model, "messages": [{"role": "user", "content": "hi"}]}


@pytest.mark.asyncio
async def test_chat_requires_a_key(client):
    resp = await client.post("/v1/chat/completions", json=_chat_body())
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_chat_rejects_model_not_in_scope(client):
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a"], [])
    resp = await client.post(
        "/v1/chat/completions",
        json=_chat_body(model="agent-z"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "model_not_found"


@pytest.mark.asyncio
async def test_chat_rejects_empty_messages(client):
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a"], [])
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "agent-a", "messages": []},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_chat_contract_valid_returns_501_until_turn_implemented(client):
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a"], [])
    resp = await client.post(
        "/v1/chat/completions",
        json=_chat_body(model="agent-a"),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "not_implemented"


@pytest.mark.asyncio
async def test_chat_revoked_key_is_rejected(client):
    store = client._transport.app.state.agent_model_keys
    token, rec = await store.mint("u1", ["agent-a"], [])
    await store.revoke(rec["id"])
    resp = await client.post(
        "/v1/chat/completions",
        json=_chat_body(),
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_chat_auth_precedes_body_validation(client):
    # An unauthenticated caller with a malformed body must get 401 (auth first),
    # never a 422 that leaks the schema.
    resp = await client.post("/v1/chat/completions", json={"bogus": True})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_api_key"


@pytest.mark.asyncio
async def test_chat_missing_model_is_openai_shaped_400(client):
    store = client._transport.app.state.agent_model_keys
    token, _ = await store.mint("u1", ["agent-a"], [])
    resp = await client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400
    # OpenAI envelope, not FastAPI's default {"detail": [...]} 422.
    assert resp.json()["error"]["type"] == "invalid_request_error"
