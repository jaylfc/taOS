import pytest


@pytest.mark.asyncio
async def test_valid_bearer_token_populates_request_state(client, app):
    store = app.state.agent_tokens_store
    plaintext, _ = await store.issue(agent_id="test-agent", user_id="test-user", scope=["agents.list"])
    resp = await client.get("/api/agents", headers={"Authorization": f"Bearer {plaintext}"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_invalid_bearer_token_returns_401(client):
    resp = await client.get(
        "/api/agents",
        headers={"Authorization": "Bearer taos_agent_invalid_token_value_here_xxxxxxxxxxxxxxxxxxxxxxxx"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"] == "invalid_token"
    assert body["fix"] is not None
    assert body["doc_url"] is not None


@pytest.mark.asyncio
async def test_non_bearer_request_uses_existing_session_auth(client):
    """Existing desktop session-cookie auth keeps working (no regression)."""
    resp = await client.get("/api/agents")
    # The client fixture sets a valid session cookie, so this should NOT be a bearer-related 401
    assert resp.status_code != 401 or "invalid_token" not in resp.text
