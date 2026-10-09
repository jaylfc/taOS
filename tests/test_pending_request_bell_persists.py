"""Test that a pending agent scope request bell is not archived by the archive route while pending (tsk-4valrt)."""
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
async def test_pending_scope_request_bell_not_archived_by_archive_route(
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

        # Agent files a scope request through the real POST route
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
        assert my_notif is not None, "Notification for the scope request not found"
        notif_id = my_notif["id"]

        # Helper to check if a notification is archived for the admin user
        async def is_archived(nid: int) -> bool:
            archived_notifs = await notifications.list_archived(user_id=admin_uid)
            archived_ids = [n["id"] for n in archived_notifs]
            return nid in archived_ids

        # Initially, it should not be archived
        assert not await is_archived(notif_id), "Notification should not be archived initially"

        # As admin, POST /api/notifications/{id}/archive for the scope-request bell while the request is pending
        # This should return 200 with kept="pending-request" and the bell should stay listed (not archived)
        archive_resp = await client.post(f"/api/notifications/{notif_id}/archive")
        assert archive_resp.status_code == 200, f"Archive failed: {archive_resp.text}"
        assert archive_resp.json() == {"ok": True, "kept": "pending-request"}, archive_resp.json()

        # Assert GET /api/notifications (admin) still LISTS that bell (positive presence, not just "not archived")
        list_resp = await client.get("/api/notifications")
        assert list_resp.status_code == 200, list_resp.text
        listed_ids = [n["id"] for n in list_resp.json()]
        assert notif_id in listed_ids, "Notification should still be listed after archive attempt"

        # And it should be marked read
        listed_notif = next(n for n in list_resp.json() if n["id"] == notif_id)
        assert listed_notif["read"] is True, "Notification should be marked read"

        # Now approve the request via the real approve route
        approve_resp = await client.post(
            f"/api/agents/registry/{cid}/scope-requests/{req_id}/approve",
            json={"granted_scopes": ["decisions_read"]},
        )
        assert approve_resp.status_code == 200, approve_resp.text
        assert approve_resp.json()["status"] == "accepted"

        # After approval, the notification should no longer be in GET /api/notifications
        list_resp = await client.get("/api/notifications")
        assert list_resp.status_code == 200
        listed_ids = [n["id"] for n in list_resp.json()]
        assert notif_id not in listed_ids, "Notification should not be listed after approval"

        # And should be in GET /api/notifications/archived
        archived_resp = await client.get("/api/notifications/archived")
        assert archived_resp.status_code == 200
        archived_ids = [n["id"] for n in archived_resp.json()]
        assert notif_id in archived_ids, "Notification should be archived after approval"

    finally:
        await registry.close()
        await grants.close()
        await scope_store.close()
        await auth_requests.close()
        await notifications.close()


@pytest.mark.asyncio
async def test_pending_auth_request_bell_not_archived_by_archive_route(
    client, monkeypatch, tmp_path
):
    app, admin_uid, registry, grants, scope_store, auth_requests, notifications, priv = await _wire(
        client, monkeypatch, tmp_path
    )
    try:
        # Create an auth request (unauthenticated agent) through the real POST route
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as agent:
            resp = await agent.post(
                "/api/agents/auth-requests",
                json={
                    "identity_claim": "test-agent",
                    "framework": "claude",
                    "requested_scopes": ["decisions_read"],
                    "reason": "test auth request",
                    "kind": "scope_request",
                },
            )
        assert resp.status_code == 200, resp.text
        req_id = resp.json()["request_id"]

        # Find the notification for this request
        all_notifs = await notifications.list()
        my_notif = None
        for n in all_notifs:
            if n.get("source") == "auth_requests":
                data = n.get("data") or {}
                if data.get("request_id") == req_id:
                    my_notif = n
                    break
        assert my_notif is not None, "Notification for the auth request not found"
        notif_id = my_notif["id"]

        async def is_archived(nid: int) -> bool:
            archived_notifs = await notifications.list_archived(user_id=admin_uid)
            archived_ids = [n["id"] for n in archived_notifs]
            return nid in archived_ids

        # Initially, it should not be archived
        assert not await is_archived(notif_id), "Notification should not be archived initially"

        # As admin, POST /api/notifications/{id}/archive for the auth-request bell while the request is pending
        # This should return 200 with kept="pending-request" and the bell should stay listed
        archive_resp = await client.post(f"/api/notifications/{notif_id}/archive")
        assert archive_resp.status_code == 200, f"Archive failed: {archive_resp.text}"
        assert archive_resp.json() == {"ok": True, "kept": "pending-request"}, archive_resp.json()

        # Assert GET /api/notifications (admin) still LISTS that bell
        list_resp = await client.get("/api/notifications")
        assert list_resp.status_code == 200, list_resp.text
        listed_ids = [n["id"] for n in list_resp.json()]
        assert notif_id in listed_ids, "Notification should still be listed after archive attempt"

        # And it should be marked read
        listed_notif = next(n for n in list_resp.json() if n["id"] == notif_id)
        assert listed_notif["read"] is True, "Notification should be marked read"

        # Now DENY the request via the real deny route
        deny_resp = await client.post(f"/api/agents/auth-requests/{req_id}/deny")
        assert deny_resp.status_code == 200, deny_resp.text
        assert deny_resp.json()["status"] == "refused"

        # After denial, the notification should no longer be in GET /api/notifications
        list_resp = await client.get("/api/notifications")
        assert list_resp.status_code == 200
        listed_ids = [n["id"] for n in list_resp.json()]
        assert notif_id not in listed_ids, "Notification should not be listed after denial"

        # And should be in GET /api/notifications/archived
        archived_resp = await client.get("/api/notifications/archived")
        assert archived_resp.status_code == 200
        archived_ids = [n["id"] for n in archived_resp.json()]
        assert notif_id in archived_ids, "Notification should be archived after denial"

    finally:
        await registry.close()
        await grants.close()
        await scope_store.close()
        await auth_requests.close()
        await notifications.close()


@pytest.mark.asyncio
async def test_normal_notification_is_archived_by_archive_route(
    client, monkeypatch, tmp_path
):
    """Control test: a normal notification (any other source) IS archived by the same route."""
    app, admin_uid, registry, grants, scope_store, auth_requests, notifications, priv = await _wire(
        client, monkeypatch, tmp_path
    )
    try:
        # Create a normal notification through the store directly (simulating a system notification)
        await notifications.add(
            title="Test Notification",
            message="This is a normal notification",
            level="info",
            source="system",
            user_id=admin_uid,
        )

        # Find the notification
        all_notifs = await notifications.list(user_id=admin_uid)
        my_notif = None
        for n in all_notifs:
            if n.get("source") == "system" and n.get("title") == "Test Notification":
                my_notif = n
                break
        assert my_notif is not None, "Normal notification not found"
        notif_id = my_notif["id"]

        async def is_archived(nid: int) -> bool:
            archived_notifs = await notifications.list_archived(user_id=admin_uid)
            archived_ids = [n["id"] for n in archived_notifs]
            return nid in archived_ids

        # Initially, it should not be archived
        assert not await is_archived(notif_id), "Notification should not be archived initially"

        # As admin, POST /api/notifications/{id}/archive for the normal notification
        # This should archive it normally (return {"ok": True})
        archive_resp = await client.post(f"/api/notifications/{notif_id}/archive")
        assert archive_resp.status_code == 200, f"Archive failed: {archive_resp.text}"
        assert archive_resp.json() == {"ok": True}, archive_resp.json()

        # Assert GET /api/notifications (admin) no longer lists it
        list_resp = await client.get("/api/notifications")
        assert list_resp.status_code == 200, list_resp.text
        listed_ids = [n["id"] for n in list_resp.json()]
        assert notif_id not in listed_ids, "Normal notification should be archived and not listed"

        # And should be in GET /api/notifications/archived
        archived_resp = await client.get("/api/notifications/archived")
        assert archived_resp.status_code == 200
        archived_ids = [n["id"] for n in archived_resp.json()]
        assert notif_id in archived_ids, "Normal notification should be in archived"

    finally:
        await registry.close()
        await grants.close()
        await scope_store.close()
        await auth_requests.close()
        await notifications.close()