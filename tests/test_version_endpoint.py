"""Coverage for GET /api/version. Locks in:
- correct shape (non-empty version string)
- works without an auth cookie (regression-lock for EXEMPT_PATHS)
- carries mdns_hostname when publisher is active,
  and omits it (sets to None) when TAOS_MDNS=0.
"""
import os
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


def test_version_endpoint_mdns_hostname_when_disabled(monkeypatch, tmp_path):
    """When TAOS_MDNS=0, the publisher is not constructed and
    mdns_hostname is None in the version response."""
    monkeypatch.setenv("TAOS_MDNS", "0")
    app = create_app(data_dir=tmp_path / "data", catalog_dir=tmp_path / "catalog")
    client = TestClient(app)
    r = client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("version"), str)
    assert body["version"]  # non-empty
    assert body.get("mdns_hostname") is None


def test_version_endpoint_mdns_hostname_when_active(monkeypatch, tmp_path):
    """When the mdns publisher is active, mdns_hostname is carried in the version response."""
    import types
    from unittest.mock import MagicMock
    monkeypatch.setenv("TAOS_MDNS", "1")
    app = create_app(data_dir=tmp_path / "data", catalog_dir=tmp_path / "catalog")
    # Publisher was attempted during lifespan; now inject a mock publisher
    # with a known hostname so the version route can return it.
    publisher = MagicMock()
    publisher.hostname = "taos.local"
    app.state.mdns_publisher = publisher
    client = TestClient(app)
    r = client.get("/api/version")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("version"), str)
    assert body["version"]  # non-empty
    assert body.get("mdns_hostname") == "taos.local"
