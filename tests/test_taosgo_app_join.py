from pathlib import Path
import tempfile

import pytest
from fastapi.testclient import TestClient

from tinyagentos.app import create_app
from tinyagentos.auth import AuthManager
from tinyagentos.middleware.csrf import _COOKIE_NAME, _TOKEN_BYTES


class TestTaosgoAppJoin:
    """Test the /api/taosgo/app-join endpoint."""
    
    def test_csrf_protected(self, app):
        """Test that app-join requires CSRF protection."""
        client = TestClient(app)
        
        # Try without CSRF token - should be rejected
        response = client.post("/api/taosgo/app-join")
        # Should be 403 because no CSRF token
        assert response.status_code in [403, 401]
    
    def test_csrf_protected_with_bearer_token(self, app, auth_mgr):
        """Test app-join with CSRF protection but with Bearer token."""
        client = TestClient(app)
        
        # Get CSRF token first by making a request to generate it
        client.get("/")
        
        # Now try with Bearer token
        bearer_token = auth_mgr.get_local_token()
        response = client.post(
            "/api/taosgo/app-join",
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "X-CSRF-Token": client.cookies.get(_COOKIE_NAME)
            }
        )
        # Should still require CSRF
        assert response.status_code in [403, 401]
    
    def test_requires_auth(self, app):
        """Test that app-join requires authentication."""
        client = TestClient(app)
        
        # Get CSRF token
        client.get("/")
        
        # Try without any auth - should be rejected
        response = client.post(
            "/api/taosgo/app-join",
            headers={"X-CSRF-Token": client.cookies.get(_COOKIE_NAME)}
        )
        # Should be 401/403 because no auth
        assert response.status_code in [401, 403]
    
    def test_implemented_placeholder(self, app, auth_mgr):
        """Test that app-join returns placeholder for now."""
        client = TestClient(app)
        
        # Get CSRF token
        client.get("/")
        
        # Try with local token (which should map to primary user)
        bearer_token = auth_mgr.get_local_token()
        response = client.post(
            "/api/taosgo/app-join",
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "X-CSRF-Token": client.cookies.get(_COOKIE_NAME)
            }
        )
        
        # Should return placeholder response (501 for now since not fully implemented)
        # In the future, this should return a proper preauth key
        assert response.status_code == 501
        data = response.json()
        assert "detail" in data
    
    def test_placeholder_response_structure(self, app, auth_mgr):
        """Test that app-join returns expected response structure for placeholder."""
        client = TestClient(app)
        
        # Get CSRF token
        client.get("/")
        
        # Try with local token
        bearer_token = auth_mgr.get_local_token()
        response = client.post(
            "/api/taosgo/app-join",
            headers={
                "Authorization": f"Bearer {bearer_token}",
                "X-CSRF-Token": client.cookies.get(_COOKIE_NAME)
            }
        )
        
        # Should return 501 with placeholder detail
        assert response.status_code == 501
        data = response.json()
        assert "detail" in data
        assert "ensure 2FA gate is enforced" in data["detail"]


@pytest.fixture
def app():
    """Create test FastAPI app."""
    app = create_app()
    return app


@pytest.fixture
def auth_mgr():
    """Create test auth manager."""
    # Create a temporary directory for the auth manager
    temp_dir = tempfile.mkdtemp()
    auth_mgr = AuthManager(Path(temp_dir))
    return auth_mgr

