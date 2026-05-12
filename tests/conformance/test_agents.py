"""B-tier conformance for AgentsApp.

Covers the canonical contract every agent relies on:
  - happy path with wide-scope bearer
  - scope-denied 403 when the scope doesn't cover the action
  - canonical 4xx error shape (slug + detail + fix + doc_url)
  - Idempotency-Key gives identical responses on repeat
  - issue/revoke token loop (plaintext-once, has_token surface)
"""
from __future__ import annotations

import uuid

import pytest


@pytest.mark.asyncio
async def test_list_succeeds_with_wide_scope(client, app):
    store = app.state.agent_tokens_store
    token, _ = await store.issue(agent_id="c-list-1", user_id="u", scope=["*"])
    resp = await client.get("/api/agents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_list_denies_narrow_scope(client, app):
    """A token scoped to ui.notify only must NOT cover agents.list."""
    store = app.state.agent_tokens_store
    token, _ = await store.issue(agent_id="c-list-2", user_id="u", scope=["ui.notify"])
    resp = await client.get("/api/agents", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"] == "scope_denied"
    assert "agents.list" in body["detail"]


@pytest.mark.asyncio
async def test_get_unknown_agent_uses_canonical_404(client, app):
    store = app.state.agent_tokens_store
    token, _ = await store.issue(agent_id="c-404", user_id="u", scope=["*"])
    resp = await client.get(
        "/api/agents/no-such-agent",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 404
    body = resp.json()
    assert set(body.keys()) == {"error", "detail", "fix", "doc_url"}
    assert body["error"] == "agent_not_found"
    assert body["fix"]
    assert body["doc_url"]


@pytest.mark.asyncio
async def test_create_is_idempotent_with_same_key(client, app):
    """Same Idempotency-Key + same body → identical response, no duplicate."""
    store = app.state.agent_tokens_store
    token, _ = await store.issue(agent_id="c-idem", user_id="u", scope=["*"])
    name = f"conf-idem-{uuid.uuid4().hex[:8]}"
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
    body = {"name": name, "host": "192.0.2.50", "qmd_index": "conf-test"}
    a = await client.post("/api/agents", headers=headers, json=body)
    b = await client.post("/api/agents", headers=headers, json=body)
    assert a.status_code == 200
    assert b.status_code == 200
    assert a.json() == b.json()
    # Only one entry was created.
    listing = (await client.get("/api/agents", headers={"Authorization": f"Bearer {token}"})).json()
    matches = [agent for agent in listing if agent["name"] == name]
    assert len(matches) == 1


@pytest.mark.asyncio
async def test_create_then_issue_token_returns_plaintext_once(client, app):
    """Full lifecycle: create agent → issue token → GET shows has_token without plaintext."""
    store = app.state.agent_tokens_store
    token, _ = await store.issue(agent_id="c-tok", user_id="u", scope=["*"])
    h = {"Authorization": f"Bearer {token}"}
    name = f"conf-tok-{uuid.uuid4().hex[:8]}"
    await client.post(
        "/api/agents",
        headers=h,
        json={"name": name, "host": "192.0.2.51", "qmd_index": "conf-test"},
    )
    issue_resp = await client.post(f"/api/agents/{name}/token/issue", headers=h)
    assert issue_resp.status_code == 200
    issue_body = issue_resp.json()
    assert "token" in issue_body
    assert issue_body["token"].startswith("taos_agent_")
    # Subsequent GET surfaces has_token but never the plaintext.
    get_resp = await client.get(f"/api/agents/{name}", headers=h)
    assert get_resp.status_code == 200
    get_body = get_resp.json()
    assert get_body.get("has_token") is True
    assert "token" not in get_body


@pytest.mark.asyncio
async def test_revoke_token_makes_old_bearer_fail(client, app):
    """After revoke, the previous plaintext returns 401 invalid_token."""
    store = app.state.agent_tokens_store
    admin_token, _ = await store.issue(agent_id="c-rev-admin", user_id="u", scope=["*"])
    h = {"Authorization": f"Bearer {admin_token}"}
    name = f"conf-rev-{uuid.uuid4().hex[:8]}"
    await client.post(
        "/api/agents",
        headers=h,
        json={"name": name, "host": "192.0.2.52", "qmd_index": "conf-test"},
    )
    issued = (await client.post(f"/api/agents/{name}/token/issue", headers=h)).json()["token"]
    # Confirm the new token authenticates
    assert (await client.get("/api/agents", headers={"Authorization": f"Bearer {issued}"})).status_code == 200
    # Revoke and re-check
    revoke_resp = await client.delete(f"/api/agents/{name}/token", headers=h)
    assert revoke_resp.status_code == 204
    resp = await client.get("/api/agents", headers={"Authorization": f"Bearer {issued}"})
    assert resp.status_code == 401
    assert resp.json()["error"] == "invalid_token"


@pytest.mark.asyncio
async def test_invalid_bearer_uses_canonical_401(client):
    """A made-up bearer returns the canonical 401 envelope."""
    resp = await client.get(
        "/api/agents",
        headers={"Authorization": "Bearer taos_agent_definitely_not_real_xxxxxxxxxxxxxxxxxxxx"},
    )
    assert resp.status_code == 401
    body = resp.json()
    assert set(body.keys()) == {"error", "detail", "fix", "doc_url"}
    assert body["error"] == "invalid_token"
