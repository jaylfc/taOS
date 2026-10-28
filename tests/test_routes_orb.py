"""HTTP-layer tests for the taOS Orb routes (routes/orb.py): GET /api/orb/status,
/api/orb/lan, /api/orb/catalog.

Hostile cases first: non-admin 403, unauthed 401/403, transport failure.
Happy path: admin returns list of orb dicts.
"""
from __future__ import annotations

import asyncio
import json
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from pathlib import Path

from tinyagentos.orb.catalog_sig import (
    CatalogFetchError,
    CatalogSignatureMissing,
    CatalogKeyUnknown,
    CatalogSignatureInvalid,
    CatalogSerialRegression,
    CatalogStateCorrupt,
)
from tinyagentos.orb.frames import TransportUnavailable
from tinyagentos.orb.catalog import Catalog


@pytest_asyncio.fixture
async def unauthed_client(app, tmp_data_dir):
    """Client with no session cookie (mirrors test_routes_cluster_ble.py)."""
    store = app.state.cluster_pairing
    await store.init()
    app.state._startup_complete = True
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await store.close()


# ---------------------------------------------------------------------------
# Admin gate: every route 403s a non-admin session
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_requires_admin(unauthed_client):
    resp = await unauthed_client.get("/api/orb/status")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_status_non_admin_session_gets_403(client, app, monkeypatch):
    monkeypatch.setattr(app.state.auth, "session_user", lambda token, user_agent=None: {"is_admin": False})
    resp = await client.get("/api/orb/status")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_admin_empty_list(client, app, monkeypatch):
    monkeypatch.setattr(
        "tinyagentos.routes.orb.list_usb_orbs",
        list
    )
    resp = await client.get("/api/orb/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"orbs": []}


@pytest.mark.asyncio
async def test_status_admin_with_two_orbs(client, app, monkeypatch):
    fake_orbs = [
        {
            "port": "/dev/ttyACM0",
            "vid": 0x303A,
            "pid": 0x1234,
            "serial_number": "SN123456",
            "description": "taOS Orb",
        },
        {
            "port": "/dev/ttyACM1",
            "vid": 0x303A,
            "pid": 0x5678,
            "serial_number": "SN789012",
            "description": "taOS Orb",
        },
    ]
    monkeypatch.setattr(
        "tinyagentos.routes.orb.list_usb_orbs",
        lambda: fake_orbs
    )
    resp = await client.get("/api/orb/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"orbs": fake_orbs}


@pytest.mark.asyncio
async def test_status_admin_transport_unavailable(client, app, monkeypatch):
    monkeypatch.setattr(
        "tinyagentos.routes.orb.list_usb_orbs",
        lambda: (_ for _ in ()).throw(TransportUnavailable("test error"))
    )
    resp = await client.get("/api/orb/status")
    assert resp.status_code == 503
    body = resp.json()
    assert body == {"error": "orb_transport_unavailable"}

# ---------------------------------------------------------------------------
# GET /api/orb/lan (LAN discovery: mDNS + Agent Buddy)
# ---------------------------------------------------------------------------

import asyncio
import threading

from tinyagentos.orb.frames import TransportUnavailable


@pytest.mark.asyncio
async def test_lan_unauthed_requires_admin(unauthed_client):
    resp = await unauthed_client.get("/api/orb/lan")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_lan_non_admin_session_gets_403(client, app, monkeypatch):
    def raise_assert(*args, **kwargs):
        raise AssertionError("lookup must not be called for non-admin")

    monkeypatch.setattr(app.state.auth, "session_user", lambda token, user_agent=None: {"is_admin": False})
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", raise_assert)
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", raise_assert)
    resp = await client.get("/api/orb/lan")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_lan_admin_both_succeed(client, app, monkeypatch):
    fake_lan = [{"name": "orb-1", "addresses": ["192.168.1.10"], "port": 5353}]
    fake_buddy = [{"device_id": "abcd1234abcd1234", "hostname": "buddy1", "address": "192.168.1.20", "port": 4666, "boot": 1}]
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", lambda: fake_lan)
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", lambda: fake_buddy)
    resp = await client.get("/api/orb/lan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lan_orbs"] == fake_lan
    assert body["agent_buddy"] == fake_buddy
    assert body["errors"] == []


@pytest.mark.asyncio
async def test_lan_admin_probe_agent_buddy_transport_unavailable(client, app, monkeypatch):
    fake_lan = [{"name": "orb-1", "addresses": ["192.168.1.10"], "port": 5353}]
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", lambda: fake_lan)
    monkeypatch.setattr(
        "tinyagentos.routes.orb.probe_agent_buddy",
        lambda: (_ for _ in ()).throw(TransportUnavailable("x")),
    )
    resp = await client.get("/api/orb/lan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lan_orbs"] == fake_lan
    assert body["agent_buddy"] == []
    assert body["errors"] == [{"source": "agent_buddy", "error": "unavailable"}]


@pytest.mark.asyncio
async def test_lan_admin_browse_lan_orbs_oserror(client, app, monkeypatch):
    fake_buddy = [{"device_id": "abcd1234abcd1234", "hostname": "buddy1", "address": "192.168.1.20", "port": 4666, "boot": 1}]
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", lambda: (_ for _ in ()).throw(OSError("x")))
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", lambda: fake_buddy)
    resp = await client.get("/api/orb/lan")
    assert resp.status_code == 200
    body = resp.json()
    assert body["lan_orbs"] == []
    assert body["agent_buddy"] == fake_buddy
    assert body["errors"] == [{"source": "mdns", "error": "unavailable"}]


@pytest.mark.asyncio
async def test_lan_admin_browse_lan_orbs_runtime_error_raised(client, app, monkeypatch):
    fake_buddy = []
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", lambda: (_ for _ in ()).throw(RuntimeError("bad")))
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", lambda: fake_buddy)
    with pytest.raises(RuntimeError):
        await client.get("/api/orb/lan")


@pytest.mark.asyncio
async def test_lan_concurrent_execution_no_sleep(client, app, monkeypatch):
    barrier = threading.Barrier(2, timeout=2)

    def fake_browse():
        barrier.wait()
        return []

    def fake_probe():
        barrier.wait()
        return []

    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", fake_browse)
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", fake_probe)
    resp = await client.get("/api/orb/lan")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# GET /api/orb/catalog (signed catalog, admin-gated)
# ---------------------------------------------------------------------------


def _make_fake_catalog(serial: int = 42) -> Catalog:
    """Create a fake Catalog with one app, two versions (one yanked)."""
    apps = {
        "test-app": {
            "id": "test-app",
            "name": "Test App",
            "versions": [
                {"version": "1.0.0", "yanked": False},
                {"version": "0.9.0", "yanked": True},
            ],
        }
    }
    return Catalog(apps=apps)


def _write_fake_catalog_json(data_dir: Path, serial: int = 42) -> None:
    """Write a fake catalog.json to the cache directory with the given serial."""
    cache_dir = data_dir / "orb" / "catalog"
    cache_dir.mkdir(parents=True, exist_ok=True)
    catalog_data = {
        "serial": serial,
        "apps": [
            {
                "id": "test-app",
                "name": "Test App",
                "versions": [
                    {"version": "1.0.0", "yanked": False},
                    {"version": "0.9.0", "yanked": True},
                ],
            }
        ],
    }
    (cache_dir / "catalog.json").write_text(json.dumps(catalog_data))


@pytest.mark.asyncio
async def test_catalog_unauthed_requires_admin(unauthed_client):
    resp = await unauthed_client.get("/api/orb/catalog")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_catalog_non_admin_session_gets_403(client, app, monkeypatch):
    def raise_assert(*args, **kwargs):
        raise AssertionError("fetch must not be called for non-admin")

    monkeypatch.setattr(app.state.auth, "session_user", lambda token, user_agent=None: {"is_admin": False})
    monkeypatch.setattr("tinyagentos.routes.orb.fetch_catalog", raise_assert)
    monkeypatch.setattr("tinyagentos.routes.orb.load_cached_catalog", raise_assert)
    resp = await client.get("/api/orb/catalog")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_catalog_admin_remote_fetch_success(client, app, monkeypatch, tmp_data_dir):
    fake_cat = _make_fake_catalog(serial=42)

    async def fake_fetch_catalog(base, cache_dir, state_path):
        _write_fake_catalog_json(tmp_data_dir, 42)
        return fake_cat

    monkeypatch.setattr("tinyagentos.routes.orb.fetch_catalog", fake_fetch_catalog)
    monkeypatch.setattr("tinyagentos.routes.orb.load_cached_catalog", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("cache must not be called")))
    app.state.config.orb = {"catalog_url": "https://example.com/catalog"}

    resp = await client.get("/api/orb/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "remote"
    assert body["serial"] == 42
    assert len(body["apps"]) == 1
    app_data = body["apps"][0]
    assert app_data["id"] == "test-app"
    assert app_data["name"] == "Test App"
    assert len(app_data["versions"]) == 2
    versions = {v["version"]: v["yanked"] for v in app_data["versions"]}
    assert versions == {"1.0.0": False, "0.9.0": True}
    # Ensure no file paths or extents are leaked
    resp_text = resp.text
    assert "/" not in resp_text or "http" not in resp_text


@pytest.mark.asyncio
async def test_catalog_admin_remote_fetch_fails_cache_succeeds(client, app, monkeypatch, tmp_data_dir):
    fake_cat = _make_fake_catalog(serial=99)

    async def fake_fetch_catalog(base, cache_dir, state_path):
        raise CatalogFetchError("network down")

    def fake_load_cached_catalog(cache_dir, state_path):
        _write_fake_catalog_json(tmp_data_dir, 99)
        return fake_cat

    monkeypatch.setattr("tinyagentos.routes.orb.fetch_catalog", fake_fetch_catalog)
    monkeypatch.setattr("tinyagentos.routes.orb.load_cached_catalog", fake_load_cached_catalog)
    app.state.config.orb = {"catalog_url": "https://example.com/catalog"}

    resp = await client.get("/api/orb/catalog")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "cache"
    assert body["serial"] == 99


@pytest.mark.asyncio
async def test_catalog_admin_remote_fetch_fails_cache_serial_regression(client, app, monkeypatch, tmp_data_dir):
    async def fake_fetch_catalog(base, cache_dir, state_path):
        raise CatalogFetchError("network down")

    def fake_load_cached_catalog(cache_dir, state_path):
        raise CatalogSerialRegression("serial regression")

    monkeypatch.setattr("tinyagentos.routes.orb.fetch_catalog", fake_fetch_catalog)
    monkeypatch.setattr("tinyagentos.routes.orb.load_cached_catalog", fake_load_cached_catalog)
    app.state.config.orb = {"catalog_url": "https://example.com/catalog"}

    resp = await client.get("/api/orb/catalog")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "orb_catalog_serial_regression"


@pytest.mark.asyncio
async def test_catalog_admin_remote_fetch_fails_cache_fetch_error(client, app, monkeypatch):
    async def fake_fetch_catalog(base, cache_dir, state_path):
        raise CatalogFetchError("network down")

    def fake_load_cached_catalog(cache_dir, state_path):
        raise CatalogFetchError("no cache")

    monkeypatch.setattr("tinyagentos.routes.orb.fetch_catalog", fake_fetch_catalog)
    monkeypatch.setattr("tinyagentos.routes.orb.load_cached_catalog", fake_load_cached_catalog)
    app.state.config.orb = {"catalog_url": "https://example.com/catalog"}

    resp = await client.get("/api/orb/catalog")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "orb_catalog_fetch_failed"


@pytest.mark.asyncio
async def test_catalog_admin_no_base_cache_fetch_error(client, app, monkeypatch):
    async def fake_fetch_catalog(base, cache_dir, state_path):
        raise AssertionError("fetch_catalog must not be called when base is empty")

    def fake_load_cached_catalog(cache_dir, state_path):
        raise CatalogFetchError("no cache")

    monkeypatch.setattr("tinyagentos.routes.orb.fetch_catalog", fake_fetch_catalog)
    monkeypatch.setattr("tinyagentos.routes.orb.load_cached_catalog", fake_load_cached_catalog)
    app.state.config.orb = {"catalog_url": ""}

    resp = await client.get("/api/orb/catalog")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "orb_catalog_unconfigured"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc_class,expected_error",
    [
        (CatalogSignatureMissing, "orb_catalog_signature_missing"),
        (CatalogKeyUnknown, "orb_catalog_key_unknown"),
        (CatalogSignatureInvalid, "orb_catalog_signature_invalid"),
        (CatalogSerialRegression, "orb_catalog_serial_regression"),
        (CatalogStateCorrupt, "orb_catalog_state_corrupt"),
    ],
)
async def test_catalog_admin_remote_fetch_signature_errors_no_fallback(
    client, app, monkeypatch, exc_class, expected_error
):
    async def fake_fetch_catalog(base, cache_dir, state_path):
        raise exc_class("error detail")

    def fake_load_cached_catalog(cache_dir, state_path):
        raise AssertionError("load_cached_catalog must not be called on signature/key/serial/state errors")

    monkeypatch.setattr("tinyagentos.routes.orb.fetch_catalog", fake_fetch_catalog)
    monkeypatch.setattr("tinyagentos.routes.orb.load_cached_catalog", fake_load_cached_catalog)
    app.state.config.orb = {"catalog_url": "https://example.com/catalog"}

    resp = await client.get("/api/orb/catalog")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == expected_error


@pytest.mark.asyncio
async def test_catalog_admin_base_http_not_https(client, app, monkeypatch):
    async def fake_fetch_catalog(base, cache_dir, state_path):
        raise AssertionError("fetch_catalog must not be called for http://")

    def fake_load_cached_catalog(cache_dir, state_path):
        raise AssertionError("load_cached_catalog must not be called for http://")

    monkeypatch.setattr("tinyagentos.routes.orb.fetch_catalog", fake_fetch_catalog)
    monkeypatch.setattr("tinyagentos.routes.orb.load_cached_catalog", fake_load_cached_catalog)
    app.state.config.orb = {"catalog_url": "http://example.com/catalog"}

    resp = await client.get("/api/orb/catalog")
    assert resp.status_code == 503
    body = resp.json()
    assert body["error"] == "orb_catalog_misconfigured"


@pytest.mark.asyncio
async def test_catalog_config_round_trip(client, app, monkeypatch, tmp_data_dir):
    """Test that orb.catalog_url survives config to-dict and reload."""
    from tinyagentos.config import AppConfig, load_config, save_config
    import yaml

    # Test with catalog_url set
    config_data_with_orb = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
        "orb": {"catalog_url": "https://example.com/catalog"},
    }
    config_path = tmp_data_dir / "config_with_orb.yaml"
    config_path.write_text(yaml.dump(config_data_with_orb))

    cfg = load_config(config_path)
    assert cfg.orb == {"catalog_url": "https://example.com/catalog"}

    # to_dict should include orb since it differs from default
    d = cfg.to_dict()
    assert d["orb"] == {"catalog_url": "https://example.com/catalog"}

    # Test without orb key (should get default)
    config_data_without_orb = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    config_path2 = tmp_data_dir / "config_without_orb.yaml"
    config_path2.write_text(yaml.dump(config_data_without_orb))

    cfg2 = load_config(config_path2)
    assert cfg2.orb == {"catalog_url": ""}

    # to_dict should NOT include orb when it's the default
    d2 = cfg2.to_dict()
    assert "orb" not in d2


# ---------------------------------------------------------------------------
# GET /api/orb/bridges (admin-gated, shared _gather_lan(), evaluate_all())
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bridges_unauthed_requires_admin(unauthed_client):
    resp = await unauthed_client.get("/api/orb/bridges")
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_bridges_non_admin_session_gets_403(client, app, monkeypatch):
    def raise_assert(*args, **kwargs):
        raise AssertionError("lookup must not be called for non-admin")

    monkeypatch.setattr(app.state.auth, "session_user", lambda token, user_agent=None: {"is_admin": False})
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", raise_assert)
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", raise_assert)
    monkeypatch.setattr("tinyagentos.routes.orb.evaluate_all", raise_assert)
    resp = await client.get("/api/orb/bridges")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_bridges_admin_one_orb_mimiclaw_target(client, app, monkeypatch):
    fake_lan = [
        {
            "name": "orb-1",
            "addresses": ["192.168.1.20"],
            "port": 80,
            "board": "x",
            "app": "mimiclaw",
            "version": "1",
        }
    ]
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", lambda: fake_lan)
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", lambda: [])
    resp = await client.get("/api/orb/bridges")
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"] == []
    bridges = body["bridges"]
    assert any(b.get("bridge_id") == "mimiclaw" for b in bridges)
    mimiclaw = next(b for b in bridges if b.get("bridge_id") == "mimiclaw")
    targets = mimiclaw["targets"]
    assert any(t.get("address") == "192.168.1.20" for t in targets)


@pytest.mark.asyncio
async def test_bridges_admin_probe_transport_unavailable(client, app, monkeypatch):
    fake_lan = [{"name": "orb-1", "addresses": ["192.168.1.10"], "port": 5353}]
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", lambda: fake_lan)
    monkeypatch.setattr(
        "tinyagentos.routes.orb.probe_agent_buddy",
        lambda: (_ for _ in ()).throw(TransportUnavailable("x")),
    )
    resp = await client.get("/api/orb/bridges")
    assert resp.status_code == 200
    body = resp.json()
    assert body["errors"] == [{"source": "agent_buddy", "error": "unavailable"}]
    assert "bridges" in body


@pytest.mark.asyncio
async def test_bridges_admin_evaluate_all_passthrough(client, app, monkeypatch):
    monkeypatch.setattr("tinyagentos.routes.orb.browse_lan_orbs", lambda: [])
    monkeypatch.setattr("tinyagentos.routes.orb.probe_agent_buddy", lambda: [])
    monkeypatch.setattr(
        "tinyagentos.routes.orb.evaluate_all",
        lambda lan_orbs, agent_buddy: [{"bridge_id": "x"}],
    )
    resp = await client.get("/api/orb/bridges")
    assert resp.status_code == 200
    body = resp.json()
    assert body["bridges"] == [{"bridge_id": "x"}]
