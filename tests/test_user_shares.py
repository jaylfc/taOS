"""Tests for the user-to-user resource sharing routes.

Covers:
  POST   /api/shares               — create share (idempotent, unknown user, self-share)
  POST   /api/shares/{id}/accept   — accept-gate (target-only, already-decided, wrong-user)
  POST   /api/shares/{id}/deny     — deny-gate (target-only, already-decided)
  DELETE /api/shares/{id}          — revoke (owner, not-found)
  GET    /api/shares               — list (out/in)
  user_can_access                   — gates on status='accepted'
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def shares_client(client, tmp_data_dir):
    """Async client with user_shares store initialised, authenticated as admin.

    Builds on the conftest client fixture (which handles auth + store init).
    """
    from tinyagentos.user_shares_store import UserSharesStore
    import secrets

    app = client._transport.app

    # Init user_shares store (lifespan not running in tests).
    store = UserSharesStore(tmp_data_dir / "user_shares.db")
    await store.init()
    app.state.user_shares = store

    # Create a target user via invite flow.
    auth = app.state.auth
    admin_record = auth.find_user("admin")
    admin_uid = admin_record["id"] if admin_record else ""

    target_record = auth.find_user("target")
    if target_record is None:
        invite_code = auth.add_user_invite("target", "admin")
        auth.complete_invite("target", invite_code, "Target User", "", "targetpass")
        target_record = auth.find_user("target")
    target_uid = target_record["id"] if target_record else ""

    # Set CSRF token so POST/PUT/DELETE routes pass verify_csrf.
    csrf_token = secrets.token_hex(32)
    client.cookies["csrf_token"] = csrf_token
    client.headers["X-CSRF-Token"] = csrf_token

    client._admin_uid = admin_uid
    client._target_uid = target_uid

    yield client

    await store.close()


@pytest_asyncio.fixture
async def shares_client_target(shares_client):
    """Async client authenticated as the target user.

    Reuses shares_client's setup and just swaps session to target user.
    """
    from httpx import ASGITransport, AsyncClient
    import secrets

    app = shares_client._transport.app
    auth = app.state.auth
    target_uid = shares_client._target_uid
    target_token = auth.create_session(user_id=target_uid, long_lived=True)

    # Set CSRF token.
    csrf_token = secrets.token_hex(32)

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": target_token, "csrf_token": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
    ) as c:
        c._target_uid = target_uid
        c._admin_uid = shares_client._admin_uid
        yield c


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestShareRoutes:

    # -- Create ----------------------------------------------------------

    async def test_create_share_returns_record(self, shares_client):
        """POST /api/shares creates a share and returns the record with status='pending'."""
        resp = await shares_client.post(
            "/api/shares",
            json={
                "resource_type": "project",
                "resource_id": "proj-1",
                "to_username": "target",
                "permission": "read",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["owner_user_id"] == shares_client._admin_uid
        assert data["shared_with_user_id"] == shares_client._target_uid
        assert data["resource_type"] == "project"
        assert data["resource_id"] == "proj-1"
        assert data["permission"] == "read"
        assert data.get("status") == "pending"
        assert "id" in data

    async def test_create_share_idempotent(self, shares_client):
        """Re-sharing the same resource+target+permission is idempotent (no duplicates)."""
        body = {
            "resource_type": "project",
            "resource_id": "proj-2",
            "to_username": "target",
            "permission": "read",
        }
        r1 = await shares_client.post("/api/shares", json=body)
        assert r1.status_code == 200

        r2 = await shares_client.post("/api/shares", json=body)
        assert r2.status_code == 200

        # Verify only one share exists for this resource — no duplicates.
        resp = await shares_client.get("/api/shares?direction=out")
        assert resp.status_code == 200
        matching = [
            s for s in resp.json()
            if s["resource_id"] == "proj-2" and s["resource_type"] == "project"
        ]
        assert len(matching) == 1

    async def test_re_share_preserves_accepted_status(
        self, shares_client, shares_client_target
    ):
        """Re-sharing an already-accepted share does not downgrade it to 'pending'
        and preserves the share id so accept/deny links remain valid."""
        body = {
            "resource_type": "project",
            "resource_id": "proj-reshare-accepted",
            "to_username": "target",
            "permission": "read",
        }

        # 1. Owner creates share → status='pending'.
        r1 = await shares_client.post("/api/shares", json=body)
        assert r1.status_code == 200
        share_id = r1.json()["id"]
        assert r1.json()["status"] == "pending"

        # 2. Target user accepts → status='accepted'.
        r_accept = await shares_client_target.post(f"/api/shares/{share_id}/accept")
        assert r_accept.status_code == 200
        assert r_accept.json()["status"] == "accepted"

        # 3. Owner re-shares (idempotent "ensure it exists").
        r2 = await shares_client.post("/api/shares", json=body)
        assert r2.status_code == 200
        assert r2.json()["status"] == "accepted", (
            "Re-sharing an accepted share must preserve 'accepted' status, "
            "not downgrade to 'pending'"
        )
        assert r2.json()["id"] == share_id, (
            "Re-sharing must preserve the share id so accept/deny links "
            "and notification/Decision references remain valid"
        )

        # 4. user_can_access still returns True.
        store = shares_client._transport.app.state.user_shares
        can = await store.user_can_access(
            "project", "proj-reshare-accepted", shares_client._target_uid
        )
        assert can is True, (
            "user_can_access must return True after re-share of an accepted share"
        )

    async def test_create_share_unknown_user_returns_404(self, shares_client):
        """Sharing with a non-existent username returns 404."""
        resp = await shares_client.post(
            "/api/shares",
            json={
                "resource_type": "project",
                "resource_id": "proj-3",
                "to_username": "nosuchuser",
                "permission": "read",
            },
        )
        assert resp.status_code == 404
        assert "nosuchuser" in resp.json()["detail"]

    async def test_create_share_self_share_returns_400(self, shares_client):
        """Sharing with yourself returns 400."""
        resp = await shares_client.post(
            "/api/shares",
            json={
                "resource_type": "project",
                "resource_id": "proj-4",
                "to_username": "admin",  # same user
                "permission": "read",
            },
        )
        assert resp.status_code == 400
        assert "yourself" in resp.json()["detail"]

    # -- Accept ----------------------------------------------------------

    async def test_accept_share_target_user(self, shares_client, shares_client_target):
        """Target user can accept a pending share; user_can_access then returns True."""
        # Create share as admin → target.
        r = await shares_client.post(
            "/api/shares",
            json={
                "resource_type": "project",
                "resource_id": "proj-accept",
                "to_username": "target",
                "permission": "read",
            },
        )
        assert r.status_code == 200
        share_id = r.json()["id"]
        assert r.json()["status"] == "pending"

        # Before accept, user_can_access returns False (status != 'accepted').
        from tinyagentos.routes.user_shares import user_can_access
        store = shares_client._transport.app.state.user_shares
        can = await store.user_can_access("project", "proj-accept", shares_client._target_uid)
        assert can is False

        # Target user accepts the share.
        resp = await shares_client_target.post(f"/api/shares/{share_id}/accept")
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

        # After accept, user_can_access returns True.
        can = await store.user_can_access("project", "proj-accept", shares_client._target_uid)
        assert can is True

    async def test_accept_share_wrong_user(self, shares_client):
        """Only the target user can accept a share — admin trying returns 403."""
        r = await shares_client.post(
            "/api/shares",
            json={
                "resource_type": "project",
                "resource_id": "proj-accept-wrong",
                "to_username": "target",
                "permission": "read",
            },
        )
        assert r.status_code == 200
        share_id = r.json()["id"]

        # Admin (not the target) tries to accept → 403.
        resp = await shares_client.post(f"/api/shares/{share_id}/accept")
        assert resp.status_code == 403
        assert "only the target user" in resp.json()["detail"]

    async def test_accept_share_already_decided(self, shares_client, shares_client_target):
        """Accepting an already-accepted share returns 409."""
        r = await shares_client.post(
            "/api/shares",
            json={
                "resource_type": "project",
                "resource_id": "proj-accept-twice",
                "to_username": "target",
                "permission": "read",
            },
        )
        assert r.status_code == 200
        share_id = r.json()["id"]

        # First accept succeeds.
        r1 = await shares_client_target.post(f"/api/shares/{share_id}/accept")
        assert r1.status_code == 200

        # Second accept returns 409.
        r2 = await shares_client_target.post(f"/api/shares/{share_id}/accept")
        assert r2.status_code == 409

    # -- Deny ------------------------------------------------------------

    async def test_deny_share_target_user(self, shares_client, shares_client_target):
        """Target user can deny a pending share; status becomes 'denied'."""
        r = await shares_client.post(
            "/api/shares",
            json={
                "resource_type": "project",
                "resource_id": "proj-deny",
                "to_username": "target",
                "permission": "read",
            },
        )
        assert r.status_code == 200
        share_id = r.json()["id"]

        resp = await shares_client_target.post(f"/api/shares/{share_id}/deny")
        assert resp.status_code == 200
        assert resp.json()["status"] == "denied"

        # After deny, user_can_access returns False.
        store = shares_client._transport.app.state.user_shares
        can = await store.user_can_access("project", "proj-deny", shares_client._target_uid)
        assert can is False

    async def test_deny_share_already_decided(self, shares_client, shares_client_target):
        """Denying an already-denied share returns 409."""
        r = await shares_client.post(
            "/api/shares",
            json={
                "resource_type": "project",
                "resource_id": "proj-deny-twice",
                "to_username": "target",
                "permission": "read",
            },
        )
        assert r.status_code == 200
        share_id = r.json()["id"]

        r1 = await shares_client_target.post(f"/api/shares/{share_id}/deny")
        assert r1.status_code == 200

        r2 = await shares_client_target.post(f"/api/shares/{share_id}/deny")
        assert r2.status_code == 409

    # -- Revoke ----------------------------------------------------------

    async def test_revoke_share_owner(self, shares_client):
        """Owner can revoke their own share; it is no longer listed."""
        r = await shares_client.post(
            "/api/shares",
            json={
                "resource_type": "project",
                "resource_id": "proj-revoke",
                "to_username": "target",
                "permission": "read",
            },
        )
        assert r.status_code == 200
        share_id = r.json()["id"]

        resp = await shares_client.delete(f"/api/shares/{share_id}")
        assert resp.status_code == 200
        assert resp.json() == {"status": "revoked", "share_id": share_id}

        # Verify share no longer listed.
        out = await shares_client.get("/api/shares?direction=out")
        assert out.status_code == 200
        matching = [s for s in out.json() if s["id"] == share_id]
        assert len(matching) == 0

    async def test_revoke_share_not_found(self, shares_client):
        """Revoking a non-existent share returns 404."""
        resp = await shares_client.delete("/api/shares/99999")
        assert resp.status_code == 404

    async def test_revoke_share_unauthorized(
        self, shares_client, shares_client_target
    ):
        """Non-owner, non-admin caller cannot revoke another user's share."""
        r = await shares_client.post(
            "/api/shares",
            json={
                "resource_type": "project",
                "resource_id": "proj-revoke-unauth",
                "to_username": "target",
                "permission": "read",
            },
        )
        assert r.status_code == 200
        share_id = r.json()["id"]

        # Target user (not the owner) tries to revoke → 403.
        resp = await shares_client_target.delete(f"/api/shares/{share_id}")
        assert resp.status_code == 403

    # -- List ------------------------------------------------------------

    async def test_list_shares_out(self, shares_client):
        """GET /api/shares?direction=out lists shares owned by the authenticated user."""
        # Create two shares.
        for i, res_id in enumerate(["proj-list-out-1", "proj-list-out-2"]):
            r = await shares_client.post(
                "/api/shares",
                json={
                    "resource_type": "project",
                    "resource_id": res_id,
                    "to_username": "target",
                    "permission": "read",
                },
            )
            assert r.status_code == 200

        resp = await shares_client.get("/api/shares?direction=out")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # At least the two we just created.
        out_ids = [s["resource_id"] for s in data if s["resource_type"] == "project"]
        assert "proj-list-out-1" in out_ids
        assert "proj-list-out-2" in out_ids

    async def test_list_shares_in(self, shares_client, shares_client_target):
        """GET /api/shares?direction=in lists shares received by the authenticated user."""
        # Create a share as admin → target.
        r = await shares_client.post(
            "/api/shares",
            json={
                "resource_type": "project",
                "resource_id": "proj-list-in",
                "to_username": "target",
                "permission": "read",
            },
        )
        assert r.status_code == 200

        # Target user lists incoming shares.
        resp = await shares_client_target.get("/api/shares?direction=in")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        in_ids = [s["resource_id"] for s in data if s["resource_type"] == "project"]
        assert "proj-list-in" in in_ids
