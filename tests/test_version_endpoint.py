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


def _make_recording_publisher(active: bool = True):
    """Create a fake MdnsPublisher class that records construction via closure."""
    constructed = {"value": False}

    class _RecordingMdnsPublisher:
        def __init__(self, *, data_dir, port):
            constructed["value"] = True
            self._data_dir = data_dir
            self._port = port
            self._hostname = "taos.local"

        @property
        def hostname(self) -> str:
            return self._hostname

        @property
        def active(self) -> bool:
            return active

        async def start(self) -> None:
            pass

        async def stop(self) -> None:
            pass

    return _RecordingMdnsPublisher, constructed


def test_version_endpoint_mdns_hostname_when_disabled(monkeypatch, tmp_path):
    """When TAOS_MDNS=0, the publisher is not constructed and
    mdns_hostname is None in the version response."""
    RecordingPublisher, constructed = _make_recording_publisher()
    monkeypatch.setenv("TAOS_MDNS", "0")

    with patch("tinyagentos.services.mdns_publisher.MdnsPublisher", RecordingPublisher):
        app = create_app(data_dir=tmp_path / "data", catalog_dir=tmp_path / "catalog")
        with TestClient(app) as client:
            r = client.get("/api/version")

    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("version"), str)
    assert body["version"]  # non-empty
    assert body.get("mdns_hostname") is None
    assert not constructed["value"], "Publisher must not be constructed when TAOS_MDNS=0"


def test_version_endpoint_mdns_publisher_constructed_when_enabled(monkeypatch, tmp_path):
    """When TAOS_MDNS is unset, the publisher IS constructed and
    mdns_hostname is carried in the version response."""
    RecordingPublisher, constructed = _make_recording_publisher()
    monkeypatch.delenv("TAOS_MDNS", raising=False)

    with patch("tinyagentos.services.mdns_publisher.MdnsPublisher", RecordingPublisher):
        app = create_app(data_dir=tmp_path / "data", catalog_dir=tmp_path / "catalog")
        with TestClient(app) as client:
            r = client.get("/api/version")

    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("version"), str)
    assert body["version"]  # non-empty
    assert body.get("mdns_hostname") == "taos.local"
    assert constructed["value"], "Publisher must be constructed when TAOS_MDNS is not 0"


def test_version_endpoint_mdns_hostname_none_when_publisher_inactive(monkeypatch, tmp_path):
    """When publisher exists but is inactive (start() failed), mdns_hostname is None."""
    RecordingPublisher, constructed = _make_recording_publisher(active=False)
    monkeypatch.delenv("TAOS_MDNS", raising=False)

    with patch("tinyagentos.services.mdns_publisher.MdnsPublisher", RecordingPublisher):
        app = create_app(data_dir=tmp_path / "data", catalog_dir=tmp_path / "catalog")
        with TestClient(app) as client:
            r = client.get("/api/version")

    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("version"), str)
    assert body["version"]  # non-empty
    assert body.get("mdns_hostname") is None
    assert constructed["value"], "Publisher must be constructed but inactive"


def test_version_endpoint_mdns_disabled_by_config_enabled_zero_string(monkeypatch, tmp_path):
    """When config.mdns.enabled is '0' (string), publisher is not constructed."""
    RecordingPublisher, constructed = _make_recording_publisher()
    monkeypatch.delenv("TAOS_MDNS", raising=False)

    # Create a config file with mdns.enabled = "0"
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    config_path = data_dir / "config.yaml"
    config_path.write_text("mdns:\n  enabled: \"0\"\n")

    with patch("tinyagentos.services.mdns_publisher.MdnsPublisher", RecordingPublisher):
        app = create_app(data_dir=data_dir, catalog_dir=tmp_path / "catalog")
        with TestClient(app) as client:
            r = client.get("/api/version")

    assert r.status_code == 200
    body = r.json()
    assert isinstance(body.get("version"), str)
    assert body["version"]  # non-empty
    assert body.get("mdns_hostname") is None
    assert not constructed["value"], "Publisher must not be constructed when config.mdns.enabled='0'"