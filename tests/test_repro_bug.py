import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token


async def _mint_agent(app, project_id, scopes, handle="@taOS-dev"):
    registry = app.state.agent_registry
    grants = app.state.agent_grants
    for store in (registry, grants):
        if store._db is None:
            await store.init()
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="claude-code", display_name="taOS dev",
        allow_reserved=True, origin="internal", handle=handle,
    )
    cid = rec["canonical_id"]
    if rec.get("status") != "active":
        await registry.set_status(cid, "active")
    for scope in scopes:
        await grants.add_grant(cid, scope, project_id=project_id)
    token = mint_registry_token(cid, priv, user_id="u", framework="claude-code", project_id=project_id)
    return cid, token


async def _new_project(client, name="alpha", slug="alpha"):
    resp = await client.post("/api/projects", json={"name": name, "slug": slug})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def _decision_body(**over):
    body = {"from_agent": "spoofed", "question": "ship it?", "type": "approve_deny"}
    body.update(over)
    return body


def _bearer_only_client(app, token):
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_repro_garbage_token_invalid_body_creates_422(client):
    app = client._transport.app
    async with _bearer_only_client(app, "garbage-token") as ac:
        resp = await ac.post("/api/decisions", json={"from_agent": "@a"})  # missing question, type -> 422
    print("CREATE garbage+invalid body ->", resp.status_code, resp.text)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_repro_garbage_token_valid_body_creates(client):
    app = client._transport.app
    async with _bearer_only_client(app, "garbage-token") as ac:
        resp = await ac.post("/api/decisions", json=_decision_body())
    print("CREATE garbage+valid body ->", resp.status_code, resp.text)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_repro_garbage_token_invalid_body_answer(client):
    app = client._transport.app
    async with _bearer_only_client(app, "garbage-token") as ac:
        resp = await ac.post("/api/decisions/dec-xyz/answer/agent", json={})  # invalid: missing value -> 422
    print("ANSWER/agent garbage+invalid body ->", resp.status_code, resp.text)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_repro_valid_agent_invalid_body_creates_422(client):
    """valid token + invalid body must still 422."""
    app = client._transport.app
    pid = await _new_project(client)
    cid, token = await _mint_agent(app, pid, ("decisions_write",))
    async with _bearer_only_client(app, token) as ac:
        resp = await ac.post("/api/decisions", json={"from_agent": "@a"})  # invalid body
    print("CREATE valid+invalid body ->", resp.status_code, resp.text)
    assert resp.status_code == 422
