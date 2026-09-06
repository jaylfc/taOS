"""Coverage for the X-Taos-Version response-header middleware.

The middleware must add the header to every response so the frontend
fetch wrapper can detect version changes opportunistically without
making a separate /api/version request."""
import pytest
from fastapi.testclient import TestClient
from tinyagentos.app import create_app


@pytest.fixture
def client(tmp_path):
    app = create_app(data_dir=tmp_path / "data", catalog_dir=tmp_path / "catalog")
    return TestClient(app)


def test_health_response_has_version_header(client):
    r = client.get("/api/health")
    assert "X-Taos-Version" in r.headers
    assert r.headers["X-Taos-Version"]


def test_version_endpoint_response_has_version_header(client):
    r = client.get("/api/version")
    assert "X-Taos-Version" in r.headers


def test_error_response_has_version_header(client):
    """Even error responses carry the header — useful so the frontend
    can detect version changes from any traffic, not just successes.
    The route doesn't exist; auth middleware may intercept with 401
    before routing returns 404 — either way the header must be present."""
    r = client.get("/api/this-does-not-exist")
    assert r.status_code in (401, 404)
    assert "X-Taos-Version" in r.headers
