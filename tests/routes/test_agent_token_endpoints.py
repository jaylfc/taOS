import pytest


@pytest.mark.asyncio
async def test_issue_token_returns_plaintext_once(client, app):
    await client.post("/api/agents", json={"name": "tok-agent", "host": "127.0.0.1", "qmd_index": "test"})
    resp = await client.post("/api/agents/tok-agent/token/issue")
    assert resp.status_code == 200
    body = resp.json()
    assert "token" in body
    assert body["token"].startswith("taos_agent_")
    # Subsequent GET shows has_token but NOT plaintext
    resp2 = await client.get("/api/agents/tok-agent")
    body2 = resp2.json()
    assert body2.get("has_token") is True
    assert "token" not in body2


@pytest.mark.asyncio
async def test_issue_revokes_previous(client, app):
    await client.post("/api/agents", json={"name": "tok-agent-2", "host": "127.0.0.1", "qmd_index": "test"})
    a = (await client.post("/api/agents/tok-agent-2/token/issue")).json()["token"]
    b = (await client.post("/api/agents/tok-agent-2/token/issue")).json()["token"]
    assert a != b
    # Old token no longer authenticates
    resp = await client.get("/api/agents", headers={"Authorization": f"Bearer {a}"})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_delete_token(client, app):
    await client.post("/api/agents", json={"name": "tok-agent-3", "host": "127.0.0.1", "qmd_index": "test"})
    token = (await client.post("/api/agents/tok-agent-3/token/issue")).json()["token"]
    resp = await client.delete("/api/agents/tok-agent-3/token")
    assert resp.status_code == 204
    resp2 = await client.get("/api/agents", headers={"Authorization": f"Bearer {token}"})
    assert resp2.status_code == 401


@pytest.mark.asyncio
async def test_issue_unknown_agent_returns_404(client):
    resp = await client.post("/api/agents/nonexistent/token/issue")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"] == "agent_not_found"
    assert body["doc_url"] is not None
