"""Scope-request flow: an EXISTING registry agent asks for MORE scopes on its
own identity, the owner/admin approves, and the grant lands on the SAME
canonical_id (no new identity is minted).

Mirrors the auth-request route tests (tests/test_routes_agent_auth_requests.py):
each test wires fresh registry / grants / scope-request stores + keypair onto
app.state via monkeypatch, so the flow is exercised end to end through the real
routes and middleware.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    load_or_create_signing_keypair,
    mint_registry_token,
)
from tinyagentos.agent_grants_store import AgentGrantsStore
from tinyagentos.agent_scope_requests_store import AgentScopeRequestsStore


class _Env:
    """Fresh stores + a registered active agent, wired onto app.state."""

    def __init__(self, registry, grants, scope_store, priv, pub, admin_uid):
        self.registry = registry
        self.grants = grants
        self.scope_store = scope_store
        self.priv = priv
        self.pub = pub
        self.admin_uid = admin_uid

    def agent_token(self, canonical_id: str, framework: str = "claude") -> str:
        return mint_registry_token(
            canonical_id, self.priv, user_id=self.admin_uid, framework=framework
        )

    async def close(self):
        await self.registry.close()
        await self.grants.close()
        await self.scope_store.close()


async def _wire(client, monkeypatch, tmp_path, *, owner_uid=None):
    """Build fresh stores, register one ACTIVE agent, monkeypatch app.state."""
    app = client._transport.app
    admin = app.state.auth.find_user("admin")
    admin_uid = admin["id"] if admin else ""
    owner_uid = owner_uid if owner_uid is not None else admin_uid

    registry = AgentRegistryStore(tmp_path / "reg.db")
    await registry.init()
    grants = AgentGrantsStore(tmp_path / "grants.db")
    await grants.init()
    scope_store = AgentScopeRequestsStore(tmp_path / "scope.db")
    await scope_store.init()
    priv, pub = load_or_create_signing_keypair(tmp_path / "keys")

    monkeypatch.setattr(app.state, "agent_registry", registry)
    monkeypatch.setattr(app.state, "agent_grants", grants)
    monkeypatch.setattr(app.state, "agent_scope_requests", scope_store)
    monkeypatch.setattr(app.state, "agent_registry_keypair", (priv, pub))

    env = _Env(registry, grants, scope_store, priv, pub, admin_uid)
    env.owner_uid = owner_uid
    return env


async def _register_active(env, *, handle="@worker", display="worker", framework="claude"):
    rec = await env.registry.register(
        framework=framework,
        display_name=display,
        user_id=env.owner_uid,
        origin="taos-deployed",  # taos-deployed registers active immediately
        handle=handle,
    )
    return rec["canonical_id"]


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_creates_and_approves_grant_on_existing_identity(
    client, monkeypatch, tmp_path
):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        before = len(await env.registry.list_all())

        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests",
            json={"requested_scopes": ["memory_read", "memory_write"], "reason": "need memory"},
        )
        assert resp.status_code == 200, resp.text
        req_id = resp.json()["request_id"]
        assert resp.json()["status"] == "pending"

        # Admin narrows to a subset on approve.
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{req_id}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["canonical_id"] == cid

        # Grant landed on the EXISTING canonical_id, and NO new identity exists.
        grants = await env.grants.list_grants(cid)
        assert {g["scope"] for g in grants} == {"memory_read"}
        assert all(g["project_id"] is None for g in grants)
        assert len(await env.registry.list_all()) == before  # no new identity
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_agent_can_self_request_with_own_token(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        token = env.agent_token(cid)
        app = client._transport.app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp = await bare.post(
                f"/api/agents/registry/{cid}/scope-requests",
                headers={"Authorization": f"Bearer {token}"},
                json={"requested_scopes": ["a2a_send"]},
            )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "pending"
        assert await env.scope_store.count_pending_for(cid) == 1
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_agent_cannot_request_for_another_identity(client, monkeypatch, tmp_path):
    """Agent A's token must not create a scope request against agent B's id."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid_a = await _register_active(env, handle="@a", display="a")
        cid_b = await _register_active(env, handle="@b", display="b")
        token_a = env.agent_token(cid_a)
        app = client._transport.app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp = await bare.post(
                f"/api/agents/registry/{cid_b}/scope-requests",
                headers={"Authorization": f"Bearer {token_a}"},
                json={"requested_scopes": ["a2a_send"]},
            )
        assert resp.status_code == 403, resp.text
        assert await env.scope_store.count_pending_for(cid_b) == 0
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_unknown_canonical_id_404(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        resp = await client.post(
            "/api/agents/registry/does-not-exist/scope-requests",
            json={"requested_scopes": ["memory_read"]},
        )
        assert resp.status_code == 404, resp.text
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_inactive_canonical_id_404(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        await env.registry.revoke(cid)  # now inactive
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests",
            json={"requested_scopes": ["memory_read"]},
        )
        assert resp.status_code == 404, resp.text
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_requested_scope_outside_vocabulary_400(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests",
            json={"requested_scopes": ["root_everything"]},
        )
        assert resp.status_code == 400, resp.text
    finally:
        await env.close()


# ---------------------------------------------------------------------------
# Approve / deny guards
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_granted_must_be_subset_of_requested_400(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        rec = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["memory_read"]
        )
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/approve",
            json={"granted_scopes": ["memory_read", "memory_write"]},
        )
        assert resp.status_code == 400, resp.text
        # Nothing was granted on the rejected approval.
        assert await env.grants.list_grants(cid) == []
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_global_decisions_write_grant_works(client, monkeypatch, tmp_path):
    """decisions_write is global-capable: a null-project grant is allowed and no
    project_id is required."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        rec = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["decisions_read", "decisions_write"]
        )
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/approve",
            json={"granted_scopes": ["decisions_read", "decisions_write"]},
        )
        assert resp.status_code == 200, resp.text
        grants = await env.grants.list_grants(cid)
        assert {g["scope"] for g in grants} == {"decisions_read", "decisions_write"}
        assert all(g["project_id"] is None for g in grants)  # global grants
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_project_scope_requires_project_id_400(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        rec = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["project_tasks"]
        )
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/approve",
            json={"granted_scopes": ["project_tasks"]},
        )
        assert resp.status_code == 400, resp.text
        assert "project_id" in resp.text
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_non_owner_cannot_approve(client, monkeypatch, tmp_path):
    """A non-admin user who does not own the agent cannot approve."""
    app = client._transport.app
    # Create a non-admin second user and its session.
    code = app.state.auth.add_user_invite("bob", "admin")
    app.state.auth.complete_invite("bob", code, "Bob", "", "bobpass123")
    bob = app.state.auth.find_user("bob")
    bob_session = app.state.auth.create_session(user_id=bob["id"], long_lived=True)

    # The agent is owned by the admin, not bob.
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        rec = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["memory_read"]
        )
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": bob_session},
        ) as bob_client:
            resp = await bob_client.post(
                f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/approve",
                json={"granted_scopes": ["memory_read"]},
            )
        assert resp.status_code == 403, resp.text
        assert await env.grants.list_grants(cid) == []
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_agent_cannot_approve_its_own_request(client, monkeypatch, tmp_path):
    """The agent's own registry token cannot reach the approve endpoint: the
    middleware allowlist only exposes the create path to a registry JWT, so an
    agent token on /approve falls through to the session gate and is rejected."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        rec = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["memory_read"]
        )
        token = env.agent_token(cid)
        app = client._transport.app
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as bare:
            resp = await bare.post(
                f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/approve",
                headers={"Authorization": f"Bearer {token}"},
                json={"granted_scopes": ["memory_read"]},
            )
        assert resp.status_code in (401, 403), resp.text
        assert await env.grants.list_grants(cid) == []
        # Request is still pending — the agent could not self-approve.
        assert (await env.scope_store.get(rec["id"]))["status"] == "pending"
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_idempotent_reapprove_no_duplicate_grant_or_identity(
    client, monkeypatch, tmp_path
):
    """Approving a scope already granted (via a fresh request) is a no-op on the
    grant table (UNIQUE key) and never creates a second identity."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        before = len(await env.registry.list_all())

        for _ in range(2):
            rec = await env.scope_store.create(
                canonical_id=cid, requested_scopes=["decisions_write"]
            )
            resp = await client.post(
                f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/approve",
                json={"granted_scopes": ["decisions_write"]},
            )
            assert resp.status_code == 200, resp.text

        grants = await env.grants.list_grants(cid)
        # Exactly one grant row for (cid, decisions_write, NULL) despite two approvals.
        assert [g["scope"] for g in grants] == ["decisions_write"]
        assert len(await env.registry.list_all()) == before  # still no new identity
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_deny_marks_refused_and_second_deny_conflicts(client, monkeypatch, tmp_path):
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        rec = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["memory_read"]
        )
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/deny",
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "refused"
        assert await env.grants.list_grants(cid) == []

        # A second deny on an already-decided request is a 409.
        resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{rec['id']}/deny",
        )
        assert resp.status_code == 409, resp.text
    finally:
        await env.close()


@pytest.mark.asyncio
async def test_approve_wrong_agent_path_404(client, monkeypatch, tmp_path):
    """A request id that belongs to a different canonical_id is not found under
    this agent's path (no cross-agent approval)."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid_a = await _register_active(env, handle="@a", display="a")
        cid_b = await _register_active(env, handle="@b", display="b")
        rec = await env.scope_store.create(
            canonical_id=cid_a, requested_scopes=["memory_read"]
        )
        resp = await client.post(
            f"/api/agents/registry/{cid_b}/scope-requests/{rec['id']}/approve",
            json={"granted_scopes": ["memory_read"]},
        )
        assert resp.status_code == 404, resp.text
    finally:
        await env.close()
