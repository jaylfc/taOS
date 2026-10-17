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


@pytest.mark.asyncio
async def test_concurrent_revoke_race(client, app, monkeypatch, tmp_path):
    """Concurrent revoke during cloud_agent member removal should succeed (tsk-diqvyc)."""
    await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    monkeypatch.setenv("TAOS_CONTROLLER_RELAY_URL", "https://relay.example.test")
    pid = await _create_project(client, slug="concur")
    cid, _token = await _invite_and_redeem(client, pid, kind="cloud_agent")
    
    # Step 1: Set up race condition by monkeypatching registry.revoke
    registry = app.state.agent_registry
    
    # Keep track of how many times revoke has been called
    revoke_call_count = 0
    original_revoke = registry.revoke
    
    async def racing_revoke(canonical_id):
        nonlocal revoke_call_count
        revoke_call_count += 1
        if revoke_call_count == 1:
            # First call succeeds (simulating concurrent DELETE)
            await original_revoke(canonical_id)
        else:
            # Second call raises ValueError (simulating lifecycle transition conflict)
            # This tests the except branch in _revoke_cloud_agent_identity
            raise ValueError("lifecycle transition conflict")
    
    monkeypatch.setattr(registry, "revoke", racing_revoke)
    
    # Step 2: Now call DELETE /api/projects/{pid}/members/{member_id}
    # The fix in _revoke_cloud_agent_identity should handle this race condition
    resp = await client.delete(f"/api/projects/{pid}/members/{cid}")
    assert resp.status_code == 200, resp.text
    
    # Step 3: Assert the member is gone (remove_member succeeded)
    members = await app.state.project_store.list_members(pid)
    assert cid not in {m["member_id"] for m in members}
    assert cid in await _revoked_ids(client)


@pytest.mark.asyncio
async def test_revoke_value_error_without_state_change_is_raised(client, app, monkeypatch, tmp_path):
    """When registry.revoke raises ValueError without state change, it propagates (tsk-diqvyc)."""
    await _setup_agent_ecosystem(app, monkeypatch, tmp_path)
    monkeypatch.setenv("TAOS_CONTROLLER_RELAY_URL", "https://relay.example.test")
    pid = await _create_project(client, slug="valerror")
    cid, _token = await _invite_and_redeem(client, pid, kind="cloud_agent")
    
    # Verify the member exists
    members = await app.state.project_store.list_members(pid)
    assert cid in {m["member_id"] for m in members}
    assert cid not in await _revoked_ids(client)
    
    # Step 1: Monkeypatch registry.revoke to raise ValueError
    registry = app.state.agent_registry
    original_revoke = registry.revoke
    
    async def raising_revoke(canonical_id):
        raise ValueError("boom")
    
    monkeypatch.setattr(registry, "revoke", raising_revoke)
    
    # Step 2: Try to DELETE the cloud_agent member, expecting error to propagate
    # Check if client raises ValueError directly (from FastAPI exception handling)
    # or if we get a 500 error instead
    try:
        resp = await client.delete(f"/api/projects/{pid}/members/{cid}")
        # If we reach here, the error was not propagated - check if it's a 500
        # This would happen if the client fixture does not raise app exceptions
        assert resp.status_code == 500, f"Expected 500 for ValueError, got {resp.status_code}: {resp.text}"
    except ValueError as e:
        # Client raised ValueError directly - this is what we want
        assert str(e) == "boom"
    
    # Step 3: Verify the member still exists (revoke failed without state change)
    members = await app.state.project_store.list_members(pid)
    assert cid in {m["member_id"] for m in members}
    assert cid not in await _revoked_ids(client)


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
