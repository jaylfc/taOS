"""An agent-filed scope request must be visible to the owner/admin (tsk-zfdagx).

The request is filed the way a live agent files it: the real POST route,
authenticated ONLY by the agent's own registry bearer token (no session
cookie). It is then read back as the admin's browser session through both list
routes the Agents app uses:

* ``GET /api/agents/scope-requests?status=pending`` (Requests tab), and
* ``GET /api/agents/registry/{cid}/scope-requests?status=pending`` (the
  per-agent strip in the Registry section).
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_grants_store import AgentGrantsStore
from tinyagentos.agent_registry_store import (
    AgentRegistryStore,
    load_or_create_signing_keypair,
    mint_registry_token,
)
from tinyagentos.agent_scope_requests_store import AgentScopeRequestsStore


async def _wire(client, monkeypatch, tmp_path):
    app = client._transport.app
    admin = app.state.auth.find_user("admin")
    admin_uid = admin["id"] if admin else ""

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
    return app, admin_uid, registry, grants, scope_store, priv


@pytest.mark.asyncio
async def test_agent_filed_pending_request_is_listed_for_admin(
    client, monkeypatch, tmp_path
):
    app, admin_uid, registry, grants, scope_store, priv = await _wire(
        client, monkeypatch, tmp_path
    )
    try:
        rec = await registry.register(
            framework="claude",
            display_name="devbot",
            user_id=admin_uid,
            origin="taos-deployed",
            handle="@devbot",
        )
        cid = rec["canonical_id"]
        token = mint_registry_token(cid, priv, user_id=admin_uid, framework="claude")

        # File it exactly as the agent does: its own bearer, no session.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as agent:
            resp = await agent.post(
                f"/api/agents/registry/{cid}/scope-requests",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "requested_scopes": ["decisions_read", "decisions_write"],
                    "reason": "ask Jay in the Decisions app",
                },
            )
        assert resp.status_code == 200, resp.text
        req_id = resp.json()["request_id"]

        # Read back as the admin's browser session, through BOTH list routes.
        cross = await client.get("/api/agents/scope-requests?status=pending")
        assert cross.status_code == 200, cross.text
        cross_rows = {r["id"]: r for r in cross.json()["requests"]}
        assert req_id in cross_rows, cross.json()
        assert cross_rows[req_id]["status"] == "pending"
        assert cross_rows[req_id]["agent_display_name"] == "devbot"

        per = await client.get(
            f"/api/agents/registry/{cid}/scope-requests?status=pending"
        )
        assert per.status_code == 200, per.text
        assert req_id in {r["id"] for r in per.json()["requests"]}, per.json()
    finally:
        await registry.close()
        await grants.close()
        await scope_store.close()
