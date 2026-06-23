"""Endpoint tests for tinyagentos/routes/userspace_apps.py."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from tinyagentos.userspace.store import UserspaceAppStore
from tinyagentos.userspace.data_store import UserspaceDataStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _init_userspace_stores(app, tmp_data_dir):
    """Initialize userspace stores the same way the lifespan does."""
    store = app.state.userspace_apps
    if store._db is not None:
        await store.close()
    await store.init()
    data_store = app.state.userspace_data
    if data_store._db is not None:
        await data_store.close()
    await data_store.init()


async def _install_test_app(store, app_id="test-app", name="Test App",
                            permissions=None, trust="community"):
    """Insert a test app directly into the store."""
    await store.install(
        app_id=app_id,
        name=name,
        version="1.0.0",
        app_type="web",
        entry="index.html",
        icon="icon.png",
        permissions_requested=permissions or [],
        trust=trust,
    )


def _make_minimal_pkg(app_id="uploaded-app", name="Uploaded App",
                       version="0.1.0", app_type="web",
                       permissions=None, entry_name="index.html"):
    """Return bytes of a valid .taosapp (zip) containing a minimal package."""
    import io
    import zipfile

    manifest = (
        f"id: {app_id}\n"
        f"name: {name}\n"
        f"version: {version}\n"
        f"app_type: {app_type}\n"
        f"entry: {entry_name}\n"
        f"icon: icon.png\n"
        f"permissions:\n"
    )
    if permissions:
        for p in permissions:
            manifest += f"  - {p}\n"
    else:
        manifest += "  []\n"

    entry = b"<html>hello</html>"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.yaml", manifest)
        zf.writestr(entry_name, entry)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# GET /api/userspace-apps/sdk.js
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sdk_returns_200(client):
    resp = await client.get("/api/userspace-apps/sdk.js")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_sdk_content_type_is_js(client):
    resp = await client.get("/api/userspace-apps/sdk.js")
    assert "javascript" in resp.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_sdk_cache_control_no_cache(client):
    resp = await client.get("/api/userspace-apps/sdk.js")
    assert resp.headers.get("cache-control") == "no-cache"


# ---------------------------------------------------------------------------
# GET /api/userspace-apps  (list_installed)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_apps_returns_200(client):
    await _init_userspace_stores(
        client._transport.app,
        client._transport.app.state.data_dir,
    )
    resp = await client.get("/api/userspace-apps")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_apps_returns_list(client):
    await _init_userspace_stores(
        client._transport.app,
        client._transport.app.state.data_dir,
    )
    data = (await client.get("/api/userspace-apps")).json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_list_apps_empty_when_none_installed(client):
    await _init_userspace_stores(
        client._transport.app,
        client._transport.app.state.data_dir,
    )
    data = (await client.get("/api/userspace-apps")).json()
    assert data == []


@pytest.mark.asyncio
async def test_list_apps_returns_installed_app(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store)
    data = (await client.get("/api/userspace-apps")).json()
    assert len(data) == 1
    assert data[0]["app_id"] == "test-app"
    assert data[0]["name"] == "Test App"


# ---------------------------------------------------------------------------
# POST /api/userspace-apps/install
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_upload_returns_200(client):
    await _init_userspace_stores(
        client._transport.app,
        client._transport.app.state.data_dir,
    )
    pkg = _make_minimal_pkg()
    resp = await client.post(
        "/api/userspace-apps/install",
        files={"package": ("test.taosapp", pkg, "application/zip")},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_install_upload_returns_app_id(client):
    await _init_userspace_stores(
        client._transport.app,
        client._transport.app.state.data_dir,
    )
    pkg = _make_minimal_pkg()
    data = (await client.post(
        "/api/userspace-apps/install",
        files={"package": ("test.taosapp", pkg, "application/zip")},
    )).json()
    assert "app_id" in data
    assert data["app_id"] == "uploaded-app"


@pytest.mark.asyncio
async def test_install_upload_returns_permissions_requested(client):
    await _init_userspace_stores(
        client._transport.app,
        client._transport.app.state.data_dir,
    )
    pkg = _make_minimal_pkg()
    data = (await client.post(
        "/api/userspace-apps/install",
        files={"package": ("test.taosapp", pkg, "application/zip")},
    )).json()
    assert "permissions_requested" in data
    assert isinstance(data["permissions_requested"], list)


@pytest.mark.asyncio
async def test_install_no_package_no_json_returns_400(client):
    await _init_userspace_stores(
        client._transport.app,
        client._transport.app.state.data_dir,
    )
    resp = await client.post(
        "/api/userspace-apps/install",
        data=b"",
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_install_json_without_source_url_returns_400(client):
    await _init_userspace_stores(
        client._transport.app,
        client._transport.app.state.data_dir,
    )
    resp = await client.post(
        "/api/userspace-apps/install",
        json={"foo": "bar"},
    )
    assert resp.status_code == 400
    assert "source_url or package required" in resp.json()["error"]


@pytest.mark.asyncio
async def test_install_private_url_returns_400(client):
    await _init_userspace_stores(
        client._transport.app,
        client._transport.app.state.data_dir,
    )
    resp = await client.post(
        "/api/userspace-apps/install",
        json={"source_url": "http://192.168.1.1/package.tgz"},
    )
    assert resp.status_code == 400
    assert "not allowed" in resp.json()["error"]


@pytest.mark.asyncio
async def test_install_container_package_returns_501(client):
    """Container app_type must be rejected with 501."""
    await _init_userspace_stores(
        client._transport.app,
        client._transport.app.state.data_dir,
    )

    manifest = (
        "id: container-app\n"
        "name: Container App\n"
        "version: 1.0.0\n"
        "app_type: container\n"
        "entry: index.html\n"
        "icon: icon.png\n"
        "permissions: []\n"
        "container:\n"
        "  image: test:latest\n"
        "  ports: [8080]\n"
    ).encode()

    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.yaml", manifest)
    pkg = buf.getvalue()

    resp = await client.post(
        "/api/userspace-apps/install",
        files={"package": ("container.taosapp", pkg, "application/zip")},
    )
    assert resp.status_code == 501
    assert "container packages are not supported" in resp.json()["error"]


# ---------------------------------------------------------------------------
# POST /api/userspace-apps/{app_id}/permissions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_permissions_returns_200(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store, permissions=["app.net"])
    resp = await client.post(
        "/api/userspace-apps/test-app/permissions",
        json={"granted": ["app.net"]},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_set_permissions_returns_granted_list(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store, permissions=["app.net", "app.agent"])
    data = (await client.post(
        "/api/userspace-apps/test-app/permissions",
        json={"granted": ["app.net", "app.agent"]},
    )).json()
    assert data["status"] == "ok"
    assert set(data["granted"]) == {"app.net", "app.agent"}


@pytest.mark.asyncio
async def test_set_permissions_unknown_app_returns_404(client):
    await _init_userspace_stores(
        client._transport.app,
        client._transport.app.state.data_dir,
    )
    resp = await client.post(
        "/api/userspace-apps/no-such-app/permissions",
        json={"granted": ["app.net"]},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_set_permissions_ignores_unrequested(client):
    """Permissions not in the app's manifest must be silently dropped."""
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store, permissions=["app.net"])
    data = (await client.post(
        "/api/userspace-apps/test-app/permissions",
        json={"granted": ["app.net", "app.llm"]},
    )).json()
    assert data["granted"] == ["app.net"]


@pytest.mark.asyncio
async def test_set_permissions_invalid_json_returns_400(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store)
    resp = await client.post(
        "/api/userspace-apps/test-app/permissions",
        content=b"not json",
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /api/userspace-apps/{app_id}/enable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enable_returns_200(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store)
    resp = await client.post("/api/userspace-apps/test-app/enable")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_enable_persists(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store)
    await client.post("/api/userspace-apps/test-app/enable")
    rec = await store.get("test-app")
    assert rec["enabled"] == 1


# ---------------------------------------------------------------------------
# POST /api/userspace-apps/{app_id}/disable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_returns_200(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store)
    resp = await client.post("/api/userspace-apps/test-app/disable")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_disable_persists(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store)
    await client.post("/api/userspace-apps/test-app/disable")
    rec = await store.get("test-app")
    assert rec["enabled"] == 0


# ---------------------------------------------------------------------------
# DELETE /api/userspace-apps/{app_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uninstall_returns_200(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store)
    resp = await client.delete("/api/userspace-apps/test-app")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_uninstall_returns_removed_true(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store)
    data = (await client.delete("/api/userspace-apps/test-app")).json()
    assert data["removed"] is True


@pytest.mark.asyncio
async def test_uninstall_returns_removed_false_for_unknown(client):
    await _init_userspace_stores(
        client._transport.app,
        client._transport.app.state.data_dir,
    )
    data = (await client.delete(
        "/api/userspace-apps/nonexistent",
    )).json()
    assert data["removed"] is False


@pytest.mark.asyncio
async def test_uninstall_removes_from_store(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store)
    await client.delete("/api/userspace-apps/test-app")
    rec = await store.get("test-app")
    assert rec is None


# ---------------------------------------------------------------------------
# GET /api/userspace-apps/{app_id}/icon
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serve_icon_no_app_returns_404(client):
    await _init_userspace_stores(
        client._transport.app,
        client._transport.app.state.data_dir,
    )
    resp = await client.get("/api/userspace-apps/no-app/icon")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_serve_icon_app_without_icon_returns_404(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store, permissions=[])
    resp = await client.get("/api/userspace-apps/test-app/icon")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/userspace-apps/{app_id}/broker
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broker_app_not_found_returns_404(client):
    await _init_userspace_stores(
        client._transport.app,
        client._transport.app.state.data_dir,
    )
    resp = await client.post(
        "/api/userspace-apps/missing-app/broker",
        json={"capability": "app.kv.get", "args": {"key": "x"}},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_broker_disabled_app_returns_404(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store)
    await store.set_enabled("test-app", False)
    resp = await client.post(
        "/api/userspace-apps/test-app/broker",
        json={"capability": "app.kv.get", "args": {"key": "x"}},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_broker_free_capability_returns_result(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store)
    resp = await client.post(
        "/api/userspace-apps/test-app/broker",
        json={"capability": "app.kv.keys", "args": {}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "result" in data


@pytest.mark.asyncio
async def test_broker_unknown_capability_returns_error(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store)
    resp = await client.post(
        "/api/userspace-apps/test-app/broker",
        json={"capability": "app.nonexistent", "args": {}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("error") == "unknown_capability"


@pytest.mark.asyncio
async def test_broker_gated_cap_without_permission_denied(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store, permissions=["app.net"])
    # app.net is NOT in permissions_granted by default, so it should be denied
    resp = await client.post(
        "/api/userspace-apps/test-app/broker",
        json={"capability": "app.net.fetch", "args": {"url": "http://example.com"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("error") == "permission_denied"


@pytest.mark.asyncio
async def test_broker_gated_cap_with_permission_allowed(client):
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store, permissions=["app.net"])
    await store.set_permissions_granted("test-app", ["app.net"])
    # app.net is now granted, but the fetch will fail since there is no real
    # network in tests. We just check the broker does not return permission_denied.
    resp = await client.post(
        "/api/userspace-apps/test-app/broker",
        json={"capability": "app.net.fetch", "args": {"url": "http://example.com"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    # The result may be an error (e.g. network unreachable), but it must NOT
    # be permission_denied since we granted the capability.
    assert data.get("error") != "permission_denied"


@pytest.mark.asyncio
async def test_broker_gated_cap_allowed_via_app_grants_ledger(client):
    # Decision 24: the broker stays the enforcer, but a per-user grant in the
    # app_grants ledger feeds it -- even when the per-app permissions_granted set
    # is empty. This proves the ledger authorises a gated capability.
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store, permissions=["app.net"])
    # Intentionally do NOT call set_permissions_granted; the only authorisation
    # comes from the ledger below.
    uid = app.state.auth.find_user("admin")["id"]
    await app.state.app_grants.set_decision(uid, "test-app", "app.net")
    resp = await client.post(
        "/api/userspace-apps/test-app/broker",
        json={"capability": "app.net.fetch", "args": {"url": "http://example.com"}},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("error") != "permission_denied"


@pytest.mark.asyncio
async def test_broker_gated_cap_denied_when_ledger_grant_absent(client):
    # The ledger only authorises what the user actually granted: a different
    # capability stays denied.
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store, permissions=["app.net", "app.memory"])
    uid = app.state.auth.find_user("admin")["id"]
    await app.state.app_grants.set_decision(uid, "test-app", "app.net")
    # app.memory was requested but neither per-app-granted nor ledger-granted.
    resp = await client.post(
        "/api/userspace-apps/test-app/broker",
        json={"capability": "app.memory.search", "args": {"query": "x"}},
    )
    assert resp.status_code == 200
    assert resp.json().get("error") == "permission_denied"


@pytest.mark.asyncio
async def test_broker_ledger_error_falls_back_to_per_app_grants(client):
    # Decision-24 merge is best-effort: if the app_grants ledger lookup raises
    # (e.g. an uninitialised store or a query error), the broker must fall back
    # to the per-app granted set rather than 500.
    app = client._transport.app
    await _init_userspace_stores(app, app.state.data_dir)
    store = app.state.userspace_apps
    await _install_test_app(store, permissions=["app.net"])
    await store.set_permissions_granted("test-app", ["app.net"])

    class _Boom:
        async def granted_capabilities(self, *a, **k):
            raise RuntimeError("ledger down")

    original = app.state.app_grants
    app.state.app_grants = _Boom()
    try:
        resp = await client.post(
            "/api/userspace-apps/test-app/broker",
            json={"capability": "app.net.fetch", "args": {"url": "http://example.com"}},
        )
    finally:
        app.state.app_grants = original
    # No 500; and the per-app grant still authorises the capability.
    assert resp.status_code == 200
    assert resp.json().get("error") != "permission_denied"
