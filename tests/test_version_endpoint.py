"""Coverage for GET /api/version. Locks in:
- correct shape (non-empty version string)
- works without an auth cookie (regression-lock for EXEMPT_PATHS)
- carries mdns_hostname when publisher is active,
  and omits it (sets to None) when TAOS_MDNS=0.
"""
import os
import pytest
from unittest.mock import MagicMock, patch
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


class _RecordingMdnsPublisher:
    """Fake MdnsPublisher that records construction and has no-op start/stop."""
    constructed = False

    def __init__(self, *, data_dir, port):
        _RecordingMdnsPublisher.constructed = True
        self._data_dir = data_dir
        self._port = port
        self._hostname = "taos.local"

    @property
    def hostname(self) -> str:
        return self._hostname

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass


def test_version_endpoint_mdns_hostname_when_disabled(monkeypatch, tmp_path):
    """When TAOS_MDNS=0, the publisher is not constructed and
    mdns_hostname is None in the version response."""
    _RecordingMdnsPublisher.constructed = False
    monkeypatch.setenv("TAOS_MDNS", "0")

    with patch("tinyagentos.services.mdns_publisher.MdnsPublisher", _RecordingMdnsPublisher):
        app = create_app(data_dir=tmp_path / "data", catalog_dir=tmp_path / "catalog")
        with TestClient(app) as client:
            r = client.get("/api/version")

    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("version"), str)
    assert body["version"]  # non-empty
    assert body.get("mdns_hostname") is None
    assert not _RecordingMdnsPublisher.constructed, "Publisher must not be constructed when TAOS_MDNS=0"


def test_version_endpoint_mdns_publisher_constructed_when_enabled(monkeypatch, tmp_path):
    """When TAOS_MDNS is unset, the publisher IS constructed and
    mdns_hostname is carried in the version response."""
    _RecordingMdnsPublisher.constructed = False
    monkeypatch.delenv("TAOS_MDNS", raising=False)

    with patch("tinyagentos.services.mdns_publisher.MdnsPublisher", _RecordingMdnsPublisher):
        app = create_app(data_dir=tmp_path / "data", catalog_dir=tmp_path / "catalog")
        with TestClient(app) as client:
            r = client.get("/api/version")

    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("version"), str)
    assert body["version"]  # non-empty
    assert body.get("mdns_hostname") == "taos.local"
    assert _RecordingMdnsPublisher.constructed, "Publisher must be constructed when TAOS_MDNS is not 0"
