"""Cross-agent scope-request list route: GET /api/agents/scope-requests.

Admin sees every agent's requests; an owner sees only agents they own;
everyone else gets 403.

Red-first tests: the route does not exist yet, so every test that hits it
will 404 before the fix lands.
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    load_or_create_signing_keypair,
    mint_registry_token,
)
from tinyagentos.agent_grants_store import AgentGrantsStore
from tinyagentos.agent_scope_requests_store import AgentScopeRequestsStore
from taos_test_csrf import csrf_event_hooks


class _Env:
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
        origin="taos-deployed",
        handle=handle,
    )
    return rec["canonical_id"]


# ---------------------------------------------------------------------------
# Admin sees requests from 2 agents
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_admin_lists_pending_across_agents(client, monkeypatch, tmp_path):
    """Admin hits GET /api/agents/scope-requests and sees every pending
    request across both registered agents."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid_a = await _register_active(env, handle="@a", display="Agent A")
        cid_b = await _register_active(env, handle="@b", display="Agent B")

        rec_a = await env.scope_store.create(
            canonical_id=cid_a, requested_scopes=["a2a_send"]
        )
        rec_b = await env.scope_store.create(
            canonical_id=cid_b, requested_scopes=["a2a_receive"]
        )

        async with AsyncClient(
            transport=ASGITransport(app=client._transport.app),
            base_url="http://test",
            cookies={"taos_session": client._cookies.get("taos_session", "")},
            event_hooks=csrf_event_hooks(),
        ) as admin_client:
            resp = await admin_client.get("/api/agents/scope-requests")
        assert resp.status_code == 200, resp.text
        rows = resp.json()["requests"]
        ids = {r["id"] for r in rows}
        assert rec_a["id"] in ids
        assert rec_b["id"] in ids
        # Display names are present.
        by_id = {r["id"]: r for r in rows}
        assert by_id[rec_a["id"]]["agent_display_name"] == "Agent A"
        assert by_id[rec_b["id"]]["agent_display_name"] == "Agent B"
    finally:
        await env.close()


# ---------------------------------------------------------------------------
# Non-owner gets 403
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_non_owner_gets_403(client, monkeypatch, tmp_path):
    """An authenticated user who owns no agents gets 403 on the cross-agent
    list route."""
    app = client._transport.app
    code = app.state.auth.add_user_invite("bob", "admin")
    app.state.auth.complete_invite("bob", code, "Bob", "", "bobpass123")
    bob = app.state.auth.find_user("bob")
    bob_session = app.state.auth.create_session(user_id=bob["id"], long_lived=True)

    env = await _wire(client, monkeypatch, tmp_path)
    try:
        # Register one agent owned by admin, not bob.
        await _register_active(env)

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": bob_session},
            event_hooks=csrf_event_hooks(),
        ) as bob_client:
            resp = await bob_client.get("/api/agents/scope-requests")
        assert resp.status_code == 403, resp.text
    finally:
        await env.close()


# ---------------------------------------------------------------------------
# Status filter works
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_filter_works(client, monkeypatch, tmp_path):
    """?status=pending returns only pending, ?status=accepted returns only
    accepted, and ?status=all returns everything."""
    env = await _wire(client, monkeypatch, tmp_path)
    try:
        cid = await _register_active(env)
        pending = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["a2a_send"]
        )
        decided = await env.scope_store.create(
            canonical_id=cid, requested_scopes=["a2a_receive"]
        )
        await env.scope_store.set_decision(
            decided["id"], "accepted", decided_by=env.admin_uid
        )

        async with AsyncClient(
            transport=ASGITransport(app=client._transport.app),
            base_url="http://test",
            cookies={"taos_session": client._cookies.get("taos_session", "")},
            event_hooks=csrf_event_hooks(),
        ) as admin_client:
            resp_pending = await admin_client.get(
                "/api/agents/scope-requests", params={"status": "pending"}
            )
            resp_accepted = await admin_client.get(
                "/api/agents/scope-requests", params={"status": "accepted"}
            )
            resp_all = await admin_client.get(
                "/api/agents/scope-requests", params={"status": "all"}
            )

        assert resp_pending.status_code == 200
        assert [r["id"] for r in resp_pending.json()["requests"]] == [pending["id"]]

        assert resp_accepted.status_code == 200
        assert [r["id"] for r in resp_accepted.json()["requests"]] == [decided["id"]]

        assert resp_all.status_code == 200
        ids = {r["id"] for r in resp_all.json()["requests"]}
        assert ids == {pending["id"], decided["id"]}
    finally:
        await env.close()


# ---------------------------------------------------------------------------
# Owner sees only their own agents
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_owner_sees_own_agents_only(client, monkeypatch, tmp_path):
    """A non-admin owner only sees requests from agents they own."""
    app = client._transport.app
    code = app.state.auth.add_user_invite("carol", "admin")
    app.state.auth.complete_invite("carol", code, "Carol", "", "carpass123")
    carol = app.state.auth.find_user("carol")
    carol_session = app.state.auth.create_session(user_id=carol["id"], long_lived=True)

    env = await _wire(client, monkeypatch, tmp_path, owner_uid=carol["id"])
    try:
        cid_carol = await _register_active(env, handle="@c", display="Carol's agent")

        # Register another agent owned by admin using the shared env's registry.
        admin_rec = await env.registry.register(
            framework="claude",
            display_name="AdminAgent",
            user_id=env.admin_uid,
            origin="taos-deployed",
            handle="@admin",
        )
        cid_admin = admin_rec["canonical_id"]

        await env.scope_store.create(
            canonical_id=cid_carol, requested_scopes=["a2a_send"]
        )
        await env.scope_store.create(
            canonical_id=cid_admin, requested_scopes=["a2a_receive"]
        )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            cookies={"taos_session": carol_session},
            event_hooks=csrf_event_hooks(),
        ) as carol_client:
            resp = await carol_client.get("/api/agents/scope-requests")
        assert resp.status_code == 200, resp.text
        rows = resp.json()["requests"]
        assert len(rows) == 1
        assert rows[0]["canonical_id"] == cid_carol
        assert rows[0]["agent_display_name"] == "Carol's agent"
    finally:
        await env.close()
