"""Tests for SharingGrantStore and sharing routes."""
import pytest
import pytest_asyncio

from tinyagentos.sharing_grants import SharingGrantStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = SharingGrantStore(tmp_path / "sharing_grants.db")
    await s.init()
    yield s
    await s.close()


class TestSharingGrantStore:
    # ── grant ──────────────────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_grant_creates_active_grant(self, store):
        grant = await store.grant("artifact-1", "app", "owner-1", "grantee1")
        assert grant["artifact_id"] == "artifact-1"
        assert grant["artifact_kind"] == "app"
        assert grant["owner_id"] == "owner-1"
        assert grant["grantee"] == "grantee1"
        assert grant["status"] == "active"
        assert grant["created_at"] > 0
        assert grant["revoked_at"] is None
        assert grant["id"].startswith("sg-")

    @pytest.mark.asyncio
    async def test_grant_dedupe_returns_existing_active(self, store):
        first = await store.grant("artifact-1", "app", "owner-1", "grantee1")
        second = await store.grant("artifact-1", "app", "owner-1", "grantee1")
        assert first["id"] == second["id"]
        assert second["status"] == "active"

    @pytest.mark.asyncio
    async def test_grant_reactivates_revoked(self, store):
        first = await store.grant("artifact-1", "app", "owner-1", "grantee1")
        await store.revoke(first["id"])
        # Re-grant same artifact + grantee should reactivate
        reactivated = await store.grant("artifact-1", "app", "owner-1", "grantee1")
        assert reactivated["id"] == first["id"]
        assert reactivated["status"] == "active"
        assert reactivated["revoked_at"] is None

    @pytest.mark.asyncio
    async def test_grant_validates_artifact_kind(self, store):
        with pytest.raises(ValueError, match="invalid artifact_kind"):
            await store.grant("artifact-1", "invalid_kind", "owner-1", "grantee1")

    @pytest.mark.asyncio
    async def test_grant_uninitialised_raises(self, tmp_path):
        store = SharingGrantStore(tmp_path / "sharing_grants.db")
        with pytest.raises(RuntimeError, match="not initialised"):
            await store.grant("artifact-1", "app", "owner-1", "grantee1")
        await store.close()

    # ── revoke ─────────────────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_revoke_sets_revoked_status(self, store):
        grant = await store.grant("artifact-1", "app", "owner-1", "grantee1")
        revoked = await store.revoke(grant["id"])
        assert revoked is not None
        assert revoked["status"] == "revoked"
        assert revoked["revoked_at"] is not None
        assert revoked["revoked_at"] >= grant["created_at"]

    @pytest.mark.asyncio
    async def test_revoke_nonexistent_returns_none(self, store):
        result = await store.revoke("sg-nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_already_revoked_returns_none(self, store):
        grant = await store.grant("artifact-1", "app", "owner-1", "grantee1")
        await store.revoke(grant["id"])
        result = await store.revoke(grant["id"])
        assert result is None

    @pytest.mark.asyncio
    async def test_revoke_uninitialised_raises(self, tmp_path):
        store = SharingGrantStore(tmp_path / "sharing_grants.db")
        with pytest.raises(RuntimeError, match="not initialised"):
            await store.revoke("sg-123")
        await store.close()

    # ── list_owned ────────────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_list_owned_returns_grants_for_owner(self, store):
        await store.grant("artifact-1", "app", "owner-1", "grantee1")
        await store.grant("artifact-2", "game", "owner-1", "grantee2")
        await store.grant("artifact-3", "project", "owner-2", "grantee1")  # different owner

        owned = await store.list_owned("owner-1")
        assert len(owned) == 2
        assert {g["artifact_id"] for g in owned} == {"artifact-1", "artifact-2"}
        # Ordered by created_at DESC (may be equal for rapid grants, so don't assert exact order)

    @pytest.mark.asyncio
    async def test_list_owned_empty_for_unknown_owner(self, store):
        owned = await store.list_owned("unknown-owner")
        assert owned == []

    @pytest.mark.asyncio
    async def test_list_owned_uninitialised_raises(self, tmp_path):
        store = SharingGrantStore(tmp_path / "sharing_grants.db")
        with pytest.raises(RuntimeError, match="not initialised"):
            await store.list_owned("owner-1")
        await store.close()

    # ── list_for ──────────────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_list_for_returns_only_active_grants(self, store):
        g1 = await store.grant("artifact-1", "app", "owner-1", "grantee1")
        g2 = await store.grant("artifact-2", "game", "owner-2", "grantee1")
        g3 = await store.grant("artifact-3", "project", "owner-3", "grantee1")
        await store.revoke(g2["id"])  # revoke one

        active = await store.list_for("grantee1")
        assert len(active) == 2
        assert {g["artifact_id"] for g in active} == {"artifact-1", "artifact-3"}
        assert all(g["status"] == "active" for g in active)

    @pytest.mark.asyncio
    async def test_list_for_by_username_and_email(self, store):
        # Grants can target username or email
        await store.grant("artifact-1", "app", "owner-1", "grantee1")
        await store.grant("artifact-2", "game", "owner-2", "grantee1@example.com")

        by_username = await store.list_for("grantee1")
        by_email = await store.list_for("grantee1@example.com")

        assert len(by_username) == 1
        assert by_username[0]["artifact_id"] == "artifact-1"
        assert len(by_email) == 1
        assert by_email[0]["artifact_id"] == "artifact-2"

    @pytest.mark.asyncio
    async def test_list_for_empty_for_unknown_grantee(self, store):
        active = await store.list_for("unknown")
        assert active == []

    @pytest.mark.asyncio
    async def test_list_for_uninitialised_raises(self, tmp_path):
        store = SharingGrantStore(tmp_path / "sharing_grants.db")
        with pytest.raises(RuntimeError, match="not initialised"):
            await store.list_for("grantee1")
        await store.close()

    @pytest.mark.asyncio
    async def test_grant_by_other_owner_does_not_reactivate(self, store):
        grant1 = await store.grant("art-1", "app", "a", "zed")
        assert grant1["owner_id"] == "a"

        await store.revoke(grant1["id"])

        assert (await store.get(grant1["id"]))["status"] == "revoked"
        assert await store.list_for("zed") == []

        with pytest.raises(PermissionError, match="artifact is shared by another owner"):
            await store.grant("art-1", "app", "b", "zed")

        assert (await store.get(grant1["id"]))["status"] == "revoked"
        assert await store.list_for("zed") == []

    # ── get ────────────────────────────────────────────────────────────
    @pytest.mark.asyncio
    async def test_get_returns_grant_by_id(self, store):
        grant = await store.grant("artifact-1", "app", "owner-1", "grantee1")
        fetched = await store.get(grant["id"])
        assert fetched is not None
        assert fetched["id"] == grant["id"]

    @pytest.mark.asyncio
    async def test_get_nonexistent_returns_none(self, store):
        result = await store.get("sg-nonexistent")
        assert result is None


# ── Route tests ───────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_create_grant_route(client):
    resp = await client.post(
        "/api/sharing/grants",
        json={"artifact_id": "artifact-1", "artifact_kind": "app", "grantee": "grantee1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["artifact_id"] == "artifact-1"
    assert data["artifact_kind"] == "app"
    assert data["grantee"] == "grantee1"
    assert data["status"] == "active"
    assert data["owner_id"] == client._transport.app.state.auth.find_user("admin")["id"]


@pytest.mark.asyncio
async def test_create_grant_dedupe_route(client):
    # First grant
    r1 = await client.post(
        "/api/sharing/grants",
        json={"artifact_id": "artifact-1", "artifact_kind": "app", "grantee": "grantee1"},
    )
    assert r1.status_code == 200
    first = r1.json()

    # Second grant for same artifact + grantee should return same grant
    r2 = await client.post(
        "/api/sharing/grants",
        json={"artifact_id": "artifact-1", "artifact_kind": "app", "grantee": "grantee1"},
    )
    assert r2.status_code == 200
    second = r2.json()
    assert first["id"] == second["id"]


@pytest.mark.asyncio
async def test_create_grant_reactivates_revoked_route(client):
    # Create then revoke
    r1 = await client.post(
        "/api/sharing/grants",
        json={"artifact_id": "artifact-1", "artifact_kind": "app", "grantee": "grantee1"},
    )
    grant = r1.json()
    await client.delete(f"/api/sharing/grants/{grant['id']}")

    # Re-grant should reactivate
    r2 = await client.post(
        "/api/sharing/grants",
        json={"artifact_id": "artifact-1", "artifact_kind": "app", "grantee": "grantee1"},
    )
    assert r2.status_code == 200
    reactivated = r2.json()
    assert reactivated["id"] == grant["id"]
    assert reactivated["status"] == "active"


@pytest.mark.asyncio
async def test_revoke_grant_route(client):
    r1 = await client.post(
        "/api/sharing/grants",
        json={"artifact_id": "artifact-1", "artifact_kind": "app", "grantee": "grantee1"},
    )
    grant = r1.json()

    resp = await client.delete(f"/api/sharing/grants/{grant['id']}")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # Verify it's revoked
    store = client._transport.app.state.sharing_grants
    revoked = await store.get(grant["id"])
    assert revoked["status"] == "revoked"


@pytest.mark.asyncio
async def test_revoke_grant_non_owner_403(client, app):
    # Create grant as admin (owner)
    r1 = await client.post(
        "/api/sharing/grants",
        json={"artifact_id": "artifact-1", "artifact_kind": "app", "grantee": "grantee1"},
    )
    grant = r1.json()

    # Create a second user
    invite_code = app.state.auth.add_user_invite("other", "admin")
    app.state.auth.complete_invite("other", invite_code, "Other User", "other@example.com", "testpass")
    other_record = app.state.auth.find_user("other")
    other_token = app.state.auth.create_session(user_id=other_record["id"], long_lived=True)

    # Try to revoke as other user
    from httpx import AsyncClient, ASGITransport
    import secrets
    csrf_token = secrets.token_hex(32)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": other_token, "csrf_token": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
    ) as other_client:
        resp = await other_client.delete(f"/api/sharing/grants/{grant['id']}")
        assert resp.status_code == 403
        assert "only the owner may revoke" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_list_owned_grants_route(client):
    await client.post(
        "/api/sharing/grants",
        json={"artifact_id": "artifact-1", "artifact_kind": "app", "grantee": "grantee1"},
    )
    await client.post(
        "/api/sharing/grants",
        json={"artifact_id": "artifact-2", "artifact_kind": "game", "grantee": "grantee2"},
    )

    resp = await client.get("/api/sharing/grants/mine")
    assert resp.status_code == 200
    grants = resp.json()
    assert len(grants) == 2
    assert {g["artifact_id"] for g in grants} == {"artifact-1", "artifact-2"}


@pytest.mark.asyncio
async def test_list_shared_with_me_route(client, app):
    # Create a second user to share with
    invite_code = app.state.auth.add_user_invite("grantee_user", "admin")
    app.state.auth.complete_invite("grantee_user", invite_code, "Grantee", "grantee@example.com", "testpass")

    # Admin shares with grantee_user by username
    await client.post(
        "/api/sharing/grants",
        json={"artifact_id": "artifact-1", "artifact_kind": "app", "grantee": "grantee_user"},
    )
    # Admin shares with grantee_user by email
    await client.post(
        "/api/sharing/grants",
        json={"artifact_id": "artifact-2", "artifact_kind": "game", "grantee": "grantee@example.com"},
    )
    # Another artifact shared with someone else
    await client.post(
        "/api/sharing/grants",
        json={"artifact_id": "artifact-3", "artifact_kind": "project", "grantee": "other_person"},
    )

    # Login as grantee_user
    grantee_record = app.state.auth.find_user("grantee_user")
    grantee_token = app.state.auth.create_session(user_id=grantee_record["id"], long_lived=True)

    from httpx import AsyncClient, ASGITransport
    import secrets
    csrf_token = secrets.token_hex(32)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": grantee_token, "csrf_token": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
    ) as grantee_client:
        resp = await grantee_client.get("/api/sharing/grants/shared-with-me")
        assert resp.status_code == 200
        grants = resp.json()
        assert len(grants) == 2
        assert {g["artifact_id"] for g in grants} == {"artifact-1", "artifact-2"}
        assert all(g["status"] == "active" for g in grants)


@pytest.mark.asyncio
async def test_list_shared_with_me_only_active(client, app):
    invite_code = app.state.auth.add_user_invite("grantee_user", "admin")
    app.state.auth.complete_invite("grantee_user", invite_code, "Grantee", "grantee@example.com", "testpass")

    r1 = await client.post(
        "/api/sharing/grants",
        json={"artifact_id": "artifact-1", "artifact_kind": "app", "grantee": "grantee_user"},
    )
    grant = r1.json()
    await client.delete(f"/api/sharing/grants/{grant['id']}")  # revoke it

    await client.post(
        "/api/sharing/grants",
        json={"artifact_id": "artifact-2", "artifact_kind": "game", "grantee": "grantee_user"},
    )

    grantee_record = app.state.auth.find_user("grantee_user")
    grantee_token = app.state.auth.create_session(user_id=grantee_record["id"], long_lived=True)

    from httpx import AsyncClient, ASGITransport
    import secrets
    csrf_token = secrets.token_hex(32)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": grantee_token, "csrf_token": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
    ) as grantee_client:
        resp = await grantee_client.get("/api/sharing/grants/shared-with-me")
        assert resp.status_code == 200
        grants = resp.json()
        assert len(grants) == 1
        assert grants[0]["artifact_id"] == "artifact-2"
        assert grants[0]["status"] == "active"


@pytest.mark.asyncio
async def test_other_owner_cannot_hijack_active_grant(client, app):
    r1 = await client.post(
        "/api/sharing/grants",
        json={"artifact_id": "art-2", "artifact_kind": "app", "grantee": "zed"},
    )
    assert r1.status_code == 200
    grant_a = r1.json()

    invite_code = app.state.auth.add_user_invite("userb", "admin")
    app.state.auth.complete_invite("userb", invite_code, "User B", "userb@example.com", "testpass")
    user_b_record = app.state.auth.find_user("userb")
    user_b_token = app.state.auth.create_session(user_id=user_b_record["id"], long_lived=True)

    from httpx import AsyncClient, ASGITransport
    import secrets
    csrf_token = secrets.token_hex(32)
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        cookies={"taos_session": user_b_token, "csrf_token": csrf_token},
        headers={"X-CSRF-Token": csrf_token},
    ) as user_b_client:
        r2 = await user_b_client.post(
            "/api/sharing/grants",
            json={"artifact_id": "art-2", "artifact_kind": "app", "grantee": "zed"},
        )
        assert r2.status_code == 403
        assert r2.json()["error"] == "artifact is shared by another owner"
        r3 = await user_b_client.get("/api/sharing/grants/mine")
        assert r3.status_code == 200
        assert r3.json() == []

        store = app.state.sharing_grants
        current_grant_a = await store.get(grant_a["id"])
        assert current_grant_a["status"] == "active"
        assert current_grant_a["owner_id"] != user_b_record["id"]