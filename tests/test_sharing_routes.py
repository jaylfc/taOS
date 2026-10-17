"""Tests for sharing routes ownership validation."""
import pytest
import pytest_asyncio
from unittest.mock import Mock, AsyncMock


class TestCreateGrantOwnership:
    """Test that create_grant validates ownership before creating grants."""
    
    @pytest.mark.asyncio
    async def test_create_grant_rejects_non_owner(self, client, app):
        """Test that a non-owner cannot create a grant for an artifact they don't own."""
        # Create user A (owner)
        owner_id = client._transport.app.state.auth.find_user("admin")["id"]
        
        # Create a project artifact owned by user A
        project_store = app.state.project_store
        project = await project_store.create_project(
            name="Test Project",
            slug="test-project",
            created_by=owner_id,
            user_id=owner_id,  # Set user_id for ownership verification
            description="Test project for ownership validation"
        )
        project_id = project["id"]
        
        # Create a second user (user B)
        invite_code = app.state.auth.add_user_invite("userb", "admin")
        app.state.auth.complete_invite("userb", invite_code, "User B", "userb@example.com", "testpass")
        user_b_record = app.state.auth.find_user("userb")
        user_b_token = app.state.auth.create_session(user_id=user_b_record["id"], long_lived=True)
        
        # Create an async client for user B
        from httpx import AsyncClient, ASGITransport
        from taos_test_csrf import bound_csrf_token
        csrf_token = bound_csrf_token(app, user_b_token)
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            cookies={"taos_session": user_b_token, "csrf_token": csrf_token},
            headers={"X-CSRF-Token": csrf_token},
        ) as user_b_client:
            # User B tries to create a grant for user A's project
            resp = await user_b_client.post(
                "/api/sharing/grants",
                json={
                    "artifact_id": project_id,
                    "artifact_kind": "project",
                    "grantee": "userb"  # Grantee is user B
                },
            )
            
            # Should be rejected with 403
            assert resp.status_code == 403
            assert resp.json()["error"] == "you do not own this artifact"
            
            # Verify that the grant was not created
            sharing_grants_store = app.state.sharing_grants
            owned_by_a = await sharing_grants_store.list_owned(owner_id)
            shared_with_user_b = await sharing_grants_store.list_for("userb")
            
            # Both should be empty
            assert owned_by_a == [], f"Expected no grants owned by A, got: {owned_by_a}"
            assert shared_with_user_b == [], f"Expected no grants shared with userb, got: {shared_with_user_b}"
    
    @pytest.mark.asyncio
    async def test_create_grant_allows_owner(self, client, app):
        """Test that an owner can create a grant for their own artifact."""
        # Get the current user (admin)
        owner_id = client._transport.app.state.auth.find_user("admin")["id"]
        
        # Create a project artifact owned by the current user
        project_store = app.state.project_store
        project = await project_store.create_project(
            name="Test Project 2",
            slug="test-project-2",
            created_by=owner_id,
            user_id=owner_id,  # Set user_id for ownership verification
            description="Test project for ownership validation"
        )
        project_id = project["id"]
        
        # Current user (admin) creates a grant for their own project
        resp = await client.post(
            "/api/sharing/grants",
            json={
                "artifact_id": project_id,
                "artifact_kind": "project",
                "grantee": "grantee1"  # Someone else to grant to
            },
        )
        
        # Should be accepted with 200
        assert resp.status_code == 200
        grant = resp.json()
        assert grant["owner_id"] == owner_id
        assert grant["artifact_id"] == project_id
        assert grant["artifact_kind"] == "project"
        assert grant["grantee"] == "grantee1"
        assert grant["status"] == "active"
        
        # Verify that the grant was created
        sharing_grants_store = app.state.sharing_grants
        owned_by_a = await sharing_grants_store.list_owned(owner_id)
        shared_with_grantee1 = await sharing_grants_store.list_for("grantee1")
        
        # Both should contain the grant
        assert len(owned_by_a) == 1
        assert len(shared_with_grantee1) == 1
        assert owned_by_a[0]["id"] == grant["id"]
        assert shared_with_grantee1[0]["id"] == grant["id"]


# ── App-kind rejection test ───────────────────────────────────────────────────
class TestCreateGrantAppKindRejectsEvenWithConsent:
    """Test that app-kind grants are always rejected, even with consent records."""
    
    @pytest.mark.asyncio
    async def test_create_grant_rejects_app_kind_even_with_app_consent(self, client, app):
        """Test that app-kind grants are rejected even with app consent through app_grants."""
        # Create user A (owner) - same as in test_create_grant_rejects_non_owner
        # user A is the admin (who owns the app_grants consent)
        # The client fixture already uses admin user, so we can use it directly
        
        # First, record app consent for "app-x" through app.state.app_grants
        # using the set_decision method
        app_grants_store = getattr(app.state, "app_grants", None)
        admin_id = client._transport.app.state.auth.find_user("admin")["id"]
        assert app_grants_store is not None
        await app_grants_store.set_decision(
            user_id=admin_id,
            app_id="app-x",
            capability="read",
            decision="granted"
        )
        
        # Try to create a grant for app-x (app-kind)
        resp = await client.post(
            "/api/sharing/grants",
            json={
                "artifact_id": "app-x",
                "artifact_kind": "app",
                "grantee": "userb"  # Someone else to grant to
            },
        )
        
        # Should be rejected with 403
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"
        assert resp.json()["error"] == "you do not own this artifact"
        
        # Verify that the sharing store holds no row for app-x
        sharing_grants_store = app.state.sharing_grants
        owned_by_a = await sharing_grants_store.list_owned(admin_id)
        shared_with_userb = await sharing_grants_store.list_for("userb")
        
        # Both should be empty (no grant created)
        assert owned_by_a == [], f"Expected no grants owned by admin, got: {owned_by_a}"
        assert shared_with_userb == [], f"Expected no grants shared with userb, got: {shared_with_userb}"