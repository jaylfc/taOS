"""Endpoint tests for tinyagentos/routes/apps.py."""

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from tinyagentos.routes.apps import OPTIONAL_FRONTEND_APPS


@pytest_asyncio.fixture(autouse=True)
async def _init_installed_apps(client):
    """Ensure installed_apps store is initialized for every test."""
    store = client._transport.app.state.installed_apps
    if store._db is not None:
        await store.close()
    await store.init()
    yield
    await store.close()


class TestListInstalledApps:
    @pytest.mark.asyncio
    async def test_empty_store_returns_200_and_empty_list(self, client):
        resp = await client.get("/api/apps/installed")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_app_without_runtime_excluded(self, client):
        store = client._transport.app.state.installed_apps
        await store.install("no-runtime-app", "1.0")
        resp = await client.get("/api/apps/installed")
        assert resp.status_code == 200
        assert resp.json() == []

    @pytest.mark.asyncio
    async def test_app_with_runtime_included(self, client):
        store = client._transport.app.state.installed_apps
        await store.install("svc-a", "1.0")
        await store.update_runtime_location("svc-a", host="10.0.0.1", port=8080, backend="lxc", ui_path="/")
        resp = await client.get("/api/apps/installed")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        item = data[0]
        assert item["app_id"] == "svc-a"
        assert item["url"] == "/apps/svc-a/"
        assert item["backend"] == "lxc"
        assert item["status"] == "running"

    @pytest.mark.asyncio
    async def test_url_uses_ui_path(self, client):
        store = client._transport.app.state.installed_apps
        await store.install("svc-sub", "1.0")
        await store.update_runtime_location("svc-sub", host="10.0.0.2", port=9090, backend="lxc", ui_path="/dashboard/")
        resp = await client.get("/api/apps/installed")
        item = next(i for i in resp.json() if i["app_id"] == "svc-sub")
        assert item["url"] == "/apps/svc-sub/dashboard/"

    @pytest.mark.asyncio
    async def test_display_name_falls_back_to_app_id(self, client):
        store = client._transport.app.state.installed_apps
        await store.install("fallback-name", "1.0")
        await store.update_runtime_location("fallback-name", host="10.0.0.3", port=7070)
        resp = await client.get("/api/apps/installed")
        item = next(i for i in resp.json() if i["app_id"] == "fallback-name")
        assert item["display_name"] == "fallback-name"

    @pytest.mark.asyncio
    async def test_generic_icon_without_manifest(self, client):
        store = client._transport.app.state.installed_apps
        await store.install("no-icon", "1.0")
        await store.update_runtime_location("no-icon", host="10.0.0.4", port=6060)
        resp = await client.get("/api/apps/installed")
        item = next(i for i in resp.json() if i["app_id"] == "no-icon")
        assert item["icon"] == "/static/app-icons/generic-service.svg"

    @pytest.mark.asyncio
    async def test_only_apps_with_runtime_returned(self, client):
        store = client._transport.app.state.installed_apps
        await store.install("with-rt", "1.0")
        await store.install("without-rt", "1.0")
        await store.update_runtime_location("with-rt", host="10.0.0.5", port=5050)
        resp = await client.get("/api/apps/installed")
        ids = {i["app_id"] for i in resp.json()}
        assert ids == {"with-rt"}

    @pytest.mark.asyncio
    async def test_response_item_has_all_keys(self, client):
        store = client._transport.app.state.installed_apps
        await store.install("shape-check", "1.0")
        await store.update_runtime_location("shape-check", host="10.0.0.6", port=4040)
        resp = await client.get("/api/apps/installed")
        item = resp.json()[0]
        for key in ("app_id", "display_name", "icon", "url", "category", "backend", "status"):
            assert key in item, f"missing key: {key}"


class TestOptionalInstalled:
    @pytest.mark.asyncio
    async def test_empty_returns_empty_list(self, client):
        resp = await client.get("/api/apps/optional/installed")
        assert resp.status_code == 200
        assert resp.json() == {"installed": []}

    @pytest.mark.asyncio
    async def test_install_then_listed(self, client):
        resp = await client.post("/api/apps/optional/coding-studio/install")
        assert resp.status_code == 200
        assert resp.json()["app_id"] == "coding-studio"
        resp = await client.get("/api/apps/optional/installed")
        assert resp.json()["installed"] == ["coding-studio"]

    @pytest.mark.asyncio
    async def test_uninstall_removes(self, client):
        await client.post("/api/apps/optional/coding-studio/install")
        resp = await client.get("/api/apps/optional/installed")
        assert "coding-studio" in resp.json()["installed"]
        resp = await client.post("/api/apps/optional/coding-studio/uninstall")
        assert resp.status_code == 200
        resp = await client.get("/api/apps/optional/installed")
        assert "coding-studio" not in resp.json()["installed"]

    @pytest.mark.asyncio
    async def test_unknown_app_rejected_install(self, client):
        resp = await client.post("/api/apps/optional/not-real/install")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_unknown_app_rejected_uninstall(self, client):
        resp = await client.post("/api/apps/optional/not-real/uninstall")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_optional_app_not_in_installed_services(self, client):
        await client.post("/api/apps/optional/coding-studio/install")
        resp = await client.get("/api/apps/installed")
        ids = {i["app_id"] for i in resp.json()}
        assert "coding-studio" not in ids


class TestOptionalCatalog:
    @pytest.mark.asyncio
    async def test_catalog_returns_all_allowlisted(self, client):
        resp = await client.get("/api/apps/optional/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert "apps" in data
        returned_ids = {a["id"] for a in data["apps"]}
        assert returned_ids == OPTIONAL_FRONTEND_APPS

    @pytest.mark.asyncio
    async def test_catalog_item_shape(self, client):
        resp = await client.get("/api/apps/optional/catalog")
        for app in resp.json()["apps"]:
            assert app["source"] == "core"
            assert "version" in app
            assert "trust" in app
            assert "installed" in app
            assert "update_available" in app

    @pytest.mark.asyncio
    async def test_install_records_version(self, client):
        from tinyagentos.routes.apps import APP_VERSIONS
        store = client._transport.app.state.installed_apps
        resp = await client.post("/api/apps/optional/coding-studio/install")
        assert resp.status_code == 200
        rows = await store.list_installed()
        row = next((r for r in rows if r["app_id"] == "coding-studio"), None)
        assert row is not None
        assert row["version"] == APP_VERSIONS["coding-studio"]

    @pytest.mark.asyncio
    async def test_update_available_false_for_fresh_install(self, client):
        await client.post("/api/apps/optional/coding-studio/install")
        resp = await client.get("/api/apps/optional/catalog")
        app = next(a for a in resp.json()["apps"] if a["id"] == "coding-studio")
        assert app["installed"] is True
        assert app["update_available"] is False

    @pytest.mark.asyncio
    async def test_update_available_true_when_stored_version_older(self, client):
        from tinyagentos.routes.apps import APP_VERSIONS
        store = client._transport.app.state.installed_apps
        await store._db.execute(
            "INSERT OR REPLACE INTO installed_apps (app_id, installed_at, version, metadata) VALUES (?, ?, ?, ?)",
            ("coding-studio", 1000.0, "0.9.0", json.dumps({"kind": "frontend-app"})),
        )
        await store._db.commit()
        resp = await client.get("/api/apps/optional/catalog")
        app = next(a for a in resp.json()["apps"] if a["id"] == "coding-studio")
        assert app["installed"] is True
        assert app["update_available"] is True
        assert app["version"] == APP_VERSIONS["coding-studio"]

    @pytest.mark.asyncio
    async def test_catalog_does_not_leak_unknown_ids(self, client):
        store = client._transport.app.state.installed_apps
        await store._db.execute(
            "INSERT OR REPLACE INTO installed_apps (app_id, installed_at, version, metadata) VALUES (?, ?, ?, ?)",
            ("unknown-app", 1000.0, "1.0.0", json.dumps({"kind": "frontend-app"})),
        )
        await store._db.commit()
        resp = await client.get("/api/apps/optional/catalog")
        returned_ids = {a["id"] for a in resp.json()["apps"]}
        assert "unknown-app" not in returned_ids
        assert returned_ids == OPTIONAL_FRONTEND_APPS


class TestSocialAppsDeseeded:
    """The platform social apps were removed from the default store."""

    def test_social_apps_not_in_allowlist(self):
        for app_id in ("reddit", "x-monitor", "github-browser", "youtube-library"):
            assert app_id not in OPTIONAL_FRONTEND_APPS

    @pytest.mark.asyncio
    async def test_social_app_install_404(self, client):
        for app_id in ("reddit", "x-monitor", "github-browser", "youtube-library"):
            resp = await client.post(f"/api/apps/optional/{app_id}/install")
            assert resp.status_code == 404, app_id

    @pytest.mark.asyncio
    async def test_social_apps_absent_from_catalog(self, client):
        resp = await client.get("/api/apps/optional/catalog")
        ids = {a["id"] for a in resp.json()["apps"]}
        assert ids.isdisjoint({"reddit", "x-monitor", "github-browser", "youtube-library"})
