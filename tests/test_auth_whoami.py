import pytest


@pytest.mark.asyncio
async def test_whoami_bearer_returns_token_identity(client, app):
    store = app.state.agent_tokens_store
    plaintext, _ = await store.issue(agent_id="who-agent", user_id="u-123", scope=["agents.list", "ui.notify"])
    resp = await client.get(
        "/api/auth/whoami",
        headers={"Authorization": f"Bearer {plaintext}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"] == "u-123"
    assert body["agent_id"] == "who-agent"
    assert body["scope"] == ["agents.list", "ui.notify"]


@pytest.mark.asyncio
async def test_whoami_session_cookie_returns_user_only(client, app):
    """A session-cookie caller (default in the client fixture) gets user_id
    populated but agent_id and scope are null — they're a human, not an
    agent token bearer."""
    resp = await client.get("/api/auth/whoami")
    assert resp.status_code == 200
    body = resp.json()
    assert body["user_id"]  # non-empty
    assert body["agent_id"] is None
    assert body["scope"] is None


@pytest.mark.asyncio
async def test_whoami_invalid_bearer_returns_401(client):
    resp = await client.get(
        "/api/auth/whoami",
        headers={"Authorization": "Bearer taos_agent_not_a_real_token_xxxxxxxxxxxxxxxxxxxx"},
    )
    assert resp.status_code == 401
