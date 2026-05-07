"""Coverage for GET /api/version. Locks in:
- correct shape (non-empty version string)
- works without an auth cookie (regression-lock for EXEMPT_PATHS)
"""
import pytest
from fastapi.testclient import TestClient
from tinyagentos.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(data_dir=tmp_path / "data", catalog_dir=tmp_path / "catalog")
    return TestClient(app)


def test_version_endpoint_returns_version(client):
    r = client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("version"), str)
    assert body["version"]  # non-empty


def test_version_endpoint_is_auth_exempt(client):
    """If this regresses, the frontend update-available toast silently
    stops firing. Asserts the EXEMPT_PATHS entry stays in place."""
    r = client.get("/api/version")  # no cookies, no headers
    assert r.status_code == 200
