"""Test that a pending agent scope request bell is not archived by clear-all or archive-read while pending (tsk-4valrt)."""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from tinyagentos.notifications import NotificationStore
from tinyagentos.agent_registry_store import mint_registry_token


async def _wire(client, monkeypatch, tmp_path):
    app = client._transport.app
    admin = app.state.auth.find_user("admin")
    admin_uid = admin["id"] if admin else ""

    # Initialize stores
    from tinyagentos.agent_registry_store import AgentRegistryStore
    from tinyagentos.agent_grants_store import AgentGrantsStore
    from tinyagentos.agent_scope_requests_store import AgentScopeRequestsStore
    from tinyagentos.auth_requests_store import AuthRequestsStore

    registry = AgentRegistryStore(tmp_path / "reg.db")
    await registry.init()
    grants = AgentGrantsStore(tmp_path / "grants.db")
    await grants.init()
    scope_store = AgentScopeRequestsStore(tmp_path / "scope.db")
    await scope_store.init()
    auth_requests = AuthRequestsStore(tmp_path / "auth_requests.db")
    await auth_requests.init()
    notifications = NotificationStore(tmp_path / "notifs.db")
    await notifications.init()
    from tinyagentos.agent_registry_store import load_or_create_signing_keypair
    priv, pub = load_or_create_signing_keypair(tmp_path / "keys")

    monkeypatch.setattr(app.state, "agent_registry", registry)
    monkeypatch.setattr(app.state, "agent_grants", grants)
    monkeypatch.setattr(app.state, "agent_scope_requests", scope_store)
    monkeypatch.setattr(app.state, "auth_requests", auth_requests)
    monkeypatch.setattr(app.state, "notifications", notifications)
    monkeypatch.setattr(app.state, "agent_registry_keypair", (priv, pub))
    return app, admin_uid, registry, grants, scope_store, auth_requests, notifications, priv


@pytest.mark.asyncio
async def test_pending_scope_request_bell_not_archived_by_clear_all(
    client, monkeypatch, tmp_path
):
    app, admin_uid, registry, grants, scope_store, auth_requests, notifications, priv = await _wire(
        client, monkeypatch, tmp_path
    )
    try:
        # Register an agent
        rec = await registry.register(
            framework="claude",
            display_name="devbot",
            user_id=admin_uid,
            origin="taos-deployed",
            handle="@devbot",
        )
        cid = rec["canonical_id"]
        token = mint_registry_token(cid, priv, user_id=admin_uid, framework="claude")

        # Agent files a scope request
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as agent:
            resp = await agent.post(
                f"/api/agents/registry/{cid}/scope-requests",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "requested_scopes": ["decisions_read"],
                    "reason": "test",
                },
            )
        assert resp.status_code == 200, resp.text
        req_id = resp.json()["request_id"]

        # Find the notification for this request by matching source and data.request_id
        all_notifs = await notifications.list()
        my_notif = None
        for n in all_notifs:
            if n.get("source") == "agent_scope_requests":
                data = n.get("data") or {}
                if data.get("request_id") == req_id:
                    my_notif = n
                    break
        assert my_notif is not None, "Notification for the request not found"
        notif_id = my_notif["id"]

        # Helper to check if a notification is archived for the admin user
        async def is_archived(nid: int) -> bool:
            archived_notifs = await notifications.list_archived(user_id=admin_uid)
            archived_ids = [n["id"] for n in archived_notifs]
            return nid in archived_ids

        # Initially, it should not be archived
        assert not await is_archived(notif_id), "Notification should not be archived initially"

        # Simulate frontend clearAll: archive all server notifications EXCEPT agent_scope_requests and auth_requests
        all_notifs = await notifications.list()
        for n in all_notifs:
            source = n.get("source")
            if source not in ("agent_scope_requests", "auth_requests"):
                await notifications.archive(n["id"])

        # After clearAll, the notification should still not be archived
        assert not await is_archived(notif_id), "Notification should not be archived after clearAll"

        # Now approve the request via the scope-requests approve endpoint
        approve_resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{req_id}/approve",
            json={"granted_scopes": ["decisions_read"]},
        )
        assert approve_resp.status_code == 200, approve_resp.text
        assert approve_resp.json()["status"] == "accepted"

        # After approval, the notification should be archived
        assert await is_archived(notif_id), "Notification should be archived after approval"

    finally:
        await registry.close()
        await grants.close()
        await scope_store.close()
        await auth_requests.close()
        await notifications.close()