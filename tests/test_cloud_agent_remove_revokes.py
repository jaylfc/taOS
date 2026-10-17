"""Removing a cloud_agent member revokes its registry token (tsk-xvc3xi, G5).

A cloud agent's identity exists only because a kind 'cloud_agent' invite was
redeemed for one project, so removing it from that project must also kill its
registry token: the canonical_id appears on GET /api/agents/registry/revoked
and the token is refused by an agent-JWT route. A member that came from a plain
kind 'agent' invite keeps today's behaviour: membership removed, identity kept.

Every step drives the real HTTP routes: mint, redeem, poll, member DELETE,
revoked feed, and an agent-JWT kanban read.
"""

import pytest


async def _create_project(client, slug) -> str:
    resp = await client.post("/api/projects", json={"name": "Remove Proj", "slug": slug})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _setup_agent_ecosystem(app, monkeypatch, tmp_path):
    from tinyagentos.agent_registry_store import (
        AgentRegistryStore,
        load_or_create_signing_keypair,
    )
    from tinyagentos.auth_requests_store import AuthRequestsStore
    from tinyagentos.agent_grants_store import AgentGrantsStore

    registry = AgentRegistryStore(tmp_path / "reg.db")
    await registry.init()
    auth_store = AuthRequestsStore(tmp_path / "auth.db")
    await auth_store.init()
    grants = AgentGrantsStore(tmp_path / "grants.db")
    await grants.init()
    priv, pub = load_or_create_signing_keypair(tmp_path / "keys")

    monkeypatch.setattr(app.state, "agent_registry", registry)
    monkeypatch.setattr(app.state, "auth_requests", auth_store)
    monkeypatch.setattr(app.state, "agent_grants", grants)
    monkeypatch.setattr(app.state, "agent_registry_keypair", (priv, pub))
    return registry


async def _invite_and_redeem(client, pid, *, kind) -> tuple[str, str]:
    payload = {"scopes": ["a2a_send"], "approval_mode": "auto"}
    if kind != "agent":
        payload["kind"] = kind
    resp = await client.post(f"/api/projects/{pid}/invites", json=payload)
    assert resp.status_code == 200, resp.text
    iid, pin = resp.json()["invite_id"], resp.json()["pin"]

    resp = await client.post(
        "/api/projects/invites/redeem",
        json={"invite_id": iid, "pin": pin, "harness": "claude"},
    )
    assert resp.status_code == 200, resp.text
    poll = await client.get(resp.json()["poll_path"])
    assert poll.status_code == 200, poll.text
    pdata = poll.json()
    assert pdata["status"] == "accepted", pdata
    return pdata["canonical_id"], pdata["token"]


async def _revoked_ids(client) -> set[str]:
    resp = await client.get("/api/agents/registry/revoked")
    assert resp.status_code == 200, resp.text
    return {r["canonical_id"] for r in resp.json()["revoked"]}


async def _tasks_with_token(client, pid, token):
    return await client.get(
        f"/api/projects/{pid}/tasks",
        headers={"Authorization": f"Bearer {token}"},
    )


@pytest.mark.asyncio
async def test_concurrent_revoke_race(client, app, monkeypatch, tmp_path):
    """Concurrent revoke during cloud_agent member removal should succeed (tsk-diqvyc)."""
    await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    monkeypatch.setenv("TAOS_CONTROLLER_RELAY_URL", "https://relay.example.test")
    pid = await _create_project(client, slug="concur")
    cid, _token = await _invite_and_redeem(client, pid, kind="cloud_agent")
    
    # Step 1: Revoke the cloud_agent identity via the registry store directly
    # This simulates the concurrent DELETE /api/agents/registry/{id} 
    registry = app.state.agent_registry
    await registry.revoke(cid)
    
    # Step 2: Now call DELETE /api/projects/{pid}/members/{member_id}
    # The fix in _revoke_cloud_agent_identity should handle this race condition
    resp = await client.delete(f"/api/projects/{pid}/members/{cid}")
    assert resp.status_code == 200, resp.text
    
    # Step 3: Assert the member is gone (remove_member succeeded)
    members = await app.state.project_store.list_members(pid)
    assert cid not in {m["member_id"] for m in members}
    assert cid in await _revoked_ids(client)


@pytest.mark.asyncio
async def test_remove_cloud_agent_member_revokes_its_token(client, app, monkeypatch, tmp_path):
    await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    monkeypatch.setenv("TAOS_CONTROLLER_RELAY_URL", "https://relay.example.test")
    pid = await _create_project(client, slug="cloudrm")
    cid, token = await _invite_and_redeem(client, pid, kind="cloud_agent")

    # The token works on an agent-JWT route before removal.
    before = await _tasks_with_token(client, pid, token)
    assert before.status_code == 200, before.text
    assert cid not in await _revoked_ids(client)

    resp = await client.delete(f"/api/projects/{pid}/members/{cid}")
    assert resp.status_code == 200, resp.text

    assert cid in await _revoked_ids(client)
    after = await _tasks_with_token(client, pid, token)
    assert after.status_code in (401, 403), after.text


@pytest.mark.asyncio
async def test_remove_plain_agent_member_keeps_identity(client, app, monkeypatch, tmp_path):
    registry = await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    pid = await _create_project(client, slug="plainrm")
    cid, _token = await _invite_and_redeem(client, pid, kind="agent")

    resp = await client.delete(f"/api/projects/{pid}/members/{cid}")
    assert resp.status_code == 200, resp.text

    members = await app.state.project_store.list_members(pid)
    assert cid not in {m["member_id"] for m in members}
    assert cid not in await _revoked_ids(client)
    record = await registry.get(cid)
    assert record["status"] == "active", record
