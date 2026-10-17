"""Tests for sharing routes ownership validation."""
import pytest
import pytest_asyncio
from tinyagentos.routes.sharing import _caller_owns_artifact
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
        import secrets
        csrf_token = secrets.token_hex(32)
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


# ── Ownership helper tests ───────────────────────────────────────────────────
class TestCallerOwnsArtifactHelper:
    """Test the _caller_owns_artifact helper function."""
    
    @pytest.mark.asyncio
    async def test_caller_owns_artifact_returns_false_for_no_store(self):
        """Test that _caller_owns_artifact returns False when no store is available."""
        mock_request = Mock()
        mock_request.app = Mock()
        mock_request.app.state = {}
        
        # Test with project kind when no project store is available
        result = await _caller_owns_artifact(
            mock_request, "project", "project-1", "user1"
        )
        assert result is False, "Should return False when project store is not available"
    
    @pytest.mark.asyncio
    async def test_caller_owns_artifact_has_correct_signature(self):
        """Test that the _caller_owns_artifact function has the correct signature."""
        import inspect
        sig = inspect.signature(_caller_owns_artifact)
        params = list(sig.parameters.keys())
        
        # Should have: request, artifact_kind, artifact_id, owner_id
        expected_params = ["request", "artifact_kind", "artifact_id", "owner_id"]
        assert all(param in params for param in expected_params), \
            f"_caller_owns_artifact should have parameters: {expected_params}"
    
    @pytest.mark.asyncio
    async def test_caller_owns_artifact_exists_in_routes(self):
        """Test that the ownership check is implemented in routes/sharing.py."""
        # This test is just to verify the function exists
        assert _caller_owns_artifact is not None, \
            "_caller_owns_artifact function should be defined"


# ── Integration tests ───────────────────────────────────────────────────────────
class TestCreateGrantIntegration:
    """Integration tests for create_grant ownership validation."""
    
    @pytest.mark.asyncio
    async def test_create_grant_has_ownership_check_in_source(self):
        """Test that create_grant includes the ownership check in its source."""
        # Read the create_grant route source
        with open("/tmp/exec-tsk-d6awig/tinyagentos/routes/sharing.py") as f:
            content = f.read()
        
        # Verify that the ownership check is present in create_grant
        assert "_caller_owns_artifact" in content, \
            "create_grant should call _caller_owns_artifact"
        
        assert "you do not own this artifact" in content, \
            "create_grant should return error message about not owning artifact"
        
        # Verify the check happens before store.grant() call
        create_grant_start = content.find("async def create_grant")
        ownership_check = content.find("_caller_owns_artifact")
        store_grant_call = content.find("await store.grant")
        
        assert ownership_check != -1, "Ownership check should be present in create_grant"
        assert store_grant_call != -1, "store.grant call should be present in create_grant"
        
        # Verify ownership check happens before store.grant
        assert ownership_check < store_grant_call, \
            "Ownership check should happen before store.grant call"
    
    @pytest.mark.asyncio
    async def test_error_message_present(self):
        """Test that the correct error message is present in create_grant."""
        # Read the create_grant route source
        with open("/tmp/exec-tsk-d6awig/tinyagentos/routes/sharing.py") as f:
            content = f.read()
        
        # Find the create_grant function
        create_grant_section = content[content.find("async def create_grant"):content.find("\n\n@router.delete")]
        assert 'return JSONResponse({"error": "you do not own this artifact"}, status_code=403)' in create_grant_section, \
            "Error should be in create_grant function"


# ── Changelog test ─────────────────────────────────────────────────────────────
class TestChangelogCreated:
    """Test that the changelog fragment was created."""
    
    @pytest.mark.asyncio
    async def test_changelog_created(self):
        """Test that the changelog fragment exists and has correct content."""
        from pathlib import Path
        
        # Check if the changelog fragment exists
        changelog_path = Path("/tmp/exec-tsk-d6awig/changelog.d/tsk-d6awig-sharing-grant-ownership.md")
        
        assert changelog_path.exists(), \
            f"Changelog fragment should exist at {changelog_path}"
        
        # Read and verify the changelog content
        with open(changelog_path) as f:
            changelog_content = f.read()
        
        # Verify it has the required sections
        assert "### Fixed" in changelog_content, \
            "Changelog should have ### Fixed section"
        
        assert "ownership verification" in changelog_content.lower() or "create_grant" in changelog_content.lower(), \
            "Changelog should mention ownership verification or create_grant"