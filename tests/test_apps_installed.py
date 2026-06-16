"""Tests for GET /api/apps/installed.

Covers:
- Empty store returns empty list.
- App installed but no runtime location is excluded.
- App with runtime location is included with correct shape.
- display_name / icon / url / category fallback logic.
- Multiple apps: only those with a runtime location are returned.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.app import create_app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def apps_app(tmp_data_dir):
    return create_app(data_dir=tmp_data_dir)


@pytest_asyncio.fixture
async def apps_client(apps_app):
    """Authenticated async client with installed_apps store initialised."""
    store = apps_app.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    await apps_app.state.qmd_client.init()

    installed_apps = apps_app.state.installed_apps
    if installed_apps._db is not None:
        await installed_apps.close()
    await installed_apps.init()

    apps_app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    rec = apps_app.state.auth.find_user("admin")
    token = apps_app.state.auth.create_session(
        user_id=rec["id"] if rec else "", long_lived=True
    )
    transport = ASGITransport(app=apps_app)
    async with AsyncClient(
        transport=transport, base_url="http://test", cookies={"taos_session": token}
    ) as c:
        yield c, apps_app.state.installed_apps

    await installed_apps.close()
    await store.close()
    await apps_app.state.qmd_client.close()
    await apps_app.state.http_client.aclose()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEmptyStore:
    @pytest.mark.asyncio
    async def test_empty_store_returns_empty_list(self, apps_client):
        client, _ = apps_client
        resp = await client.get("/api/apps/installed")
        assert resp.status_code == 200
        assert resp.json() == []


class TestNoRuntimeLocation:
    @pytest.mark.asyncio
    async def test_app_without_runtime_excluded(self, apps_client):
        """Apps that have no runtime location are not listed as desktop icons."""
        client, store = apps_client
        await store.install("some-app", "1.0")
        # Deliberately do NOT call update_runtime_location.
        resp = await client.get("/api/apps/installed")
        assert resp.status_code == 200
        assert resp.json() == []


class TestWithRuntimeLocation:
    @pytest.mark.asyncio
    async def test_app_with_runtime_location_is_included(self, apps_client):
        client, store = apps_client
        await store.install("my-svc", "1.0")
        await store.update_runtime_location("my-svc", host="10.0.0.2", port=3000, backend="lxc", ui_path="/")

        resp = await client.get("/api/apps/installed")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        item = data[0]
        assert item["app_id"] == "my-svc"
        assert item["url"] == "/apps/my-svc/"
        assert item["backend"] == "lxc"
        assert item["status"] == "running"

    @pytest.mark.asyncio
    async def test_url_construction_uses_ui_path(self, apps_client):
        client, store = apps_client
        await store.install("svc-subpath", "1.0")
        await store.update_runtime_location(
            "svc-subpath", host="10.0.0.3", port=8080, backend="lxc", ui_path="/app/"
        )

        resp = await client.get("/api/apps/installed")
        assert resp.status_code == 200
        item = next(i for i in resp.json() if i["app_id"] == "svc-subpath")
        assert item["url"] == "/apps/svc-subpath/app/"

    @pytest.mark.asyncio
    async def test_display_name_falls_back_to_app_id(self, apps_client):
        """When there is no manifest, display_name falls back to app_id."""
        client, store = apps_client
        await store.install("no-manifest-app", "1.0")
        await store.update_runtime_location("no-manifest-app", host="10.0.0.4", port=9000)

        resp = await client.get("/api/apps/installed")
        item = next(i for i in resp.json() if i["app_id"] == "no-manifest-app")
        assert item["display_name"] == "no-manifest-app"

    @pytest.mark.asyncio
    async def test_generic_icon_when_no_manifest(self, apps_client):
        client, store = apps_client
        await store.install("iconless", "1.0")
        await store.update_runtime_location("iconless", host="10.0.0.5", port=7000)

        resp = await client.get("/api/apps/installed")
        item = next(i for i in resp.json() if i["app_id"] == "iconless")
        assert item["icon"] == "/static/app-icons/generic-service.svg"


class TestMultipleApps:
    @pytest.mark.asyncio
    async def test_only_apps_with_runtime_returned(self, apps_client):
        """Install 3 apps; only 2 have runtime locations. Expect 2 in response."""
        client, store = apps_client
        await store.install("app-a", "1.0")
        await store.install("app-b", "1.0")
        await store.install("app-c", "1.0")

        await store.update_runtime_location("app-a", host="10.0.0.10", port=3001)
        await store.update_runtime_location("app-c", host="10.0.0.12", port=3003)
        # app-b intentionally has no runtime location

        resp = await client.get("/api/apps/installed")
        assert resp.status_code == 200
        ids = {i["app_id"] for i in resp.json()}
        assert ids == {"app-a", "app-c"}
        assert "app-b" not in ids

    @pytest.mark.asyncio
    async def test_response_shape_is_complete(self, apps_client):
        """Every response item must carry all expected keys."""
        client, store = apps_client
        await store.install("shape-test", "1.0")
        await store.update_runtime_location("shape-test", host="10.0.0.20", port=4000)

        resp = await client.get("/api/apps/installed")
        item = resp.json()[0]
        for key in ("app_id", "display_name", "icon", "url", "category", "backend", "status"):
            assert key in item, f"Missing key: {key}"


class TestOptionalApps:
    @pytest.mark.asyncio
    async def test_install_then_listed(self, apps_client):
        client, _ = apps_client
        # Nothing installed initially.
        resp = await client.get("/api/apps/optional/installed")
        assert resp.status_code == 200
        assert resp.json() == {"installed": []}

        # Install an allowlisted optional app.
        resp = await client.post("/api/apps/optional/reddit/install")
        assert resp.status_code == 200
        assert resp.json()["app_id"] == "reddit"

        resp = await client.get("/api/apps/optional/installed")
        assert resp.json()["installed"] == ["reddit"]

    @pytest.mark.asyncio
    async def test_uninstall_removes(self, apps_client):
        client, _ = apps_client
        await client.post("/api/apps/optional/x-monitor/install")
        resp = await client.get("/api/apps/optional/installed")
        assert "x-monitor" in resp.json()["installed"]

        resp = await client.post("/api/apps/optional/x-monitor/uninstall")
        assert resp.status_code == 200
        resp = await client.get("/api/apps/optional/installed")
        assert "x-monitor" not in resp.json()["installed"]

    @pytest.mark.asyncio
    async def test_unknown_app_rejected(self, apps_client):
        client, _ = apps_client
        resp = await client.post("/api/apps/optional/not-a-real-app/install")
        assert resp.status_code == 404
        resp = await client.post("/api/apps/optional/not-a-real-app/uninstall")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_optional_apps_excluded_from_services_list(self, apps_client):
        """Installed optional frontend apps must NOT show as proxy services
        (they have no runtime location)."""
        client, _ = apps_client
        await client.post("/api/apps/optional/github-browser/install")
        resp = await client.get("/api/apps/installed")
        ids = {i["app_id"] for i in resp.json()}
        assert "github-browser" not in ids


class TestOptionalAppCatalog:
    @pytest.mark.asyncio
    async def test_catalog_returns_all_allowlisted_apps(self, apps_client):
        """Catalog must include every id in OPTIONAL_FRONTEND_APPS with source=core."""
        from tinyagentos.routes.apps import OPTIONAL_FRONTEND_APPS
        client, _ = apps_client
        resp = await client.get("/api/apps/optional/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert "apps" in data
        returned_ids = {a["id"] for a in data["apps"]}
        assert returned_ids == OPTIONAL_FRONTEND_APPS
        for app in data["apps"]:
            assert app["source"] == "core"
            assert "version" in app
            assert "trust" in app
            assert "installed" in app
            assert "update_available" in app

    @pytest.mark.asyncio
    async def test_install_records_app_versions_version(self, apps_client):
        """Installing an app should record the APP_VERSIONS version in the DB."""
        from tinyagentos.routes.apps import APP_VERSIONS
        client, store = apps_client
        resp = await client.post("/api/apps/optional/reddit/install")
        assert resp.status_code == 200
        rows = await store.list_installed()
        row = next((r for r in rows if r["app_id"] == "reddit"), None)
        assert row is not None
        assert row["version"] == APP_VERSIONS["reddit"]

    @pytest.mark.asyncio
    async def test_update_available_false_for_fresh_install(self, apps_client):
        """A freshly installed app records the current version, so update_available=false."""
        client, _ = apps_client
        await client.post("/api/apps/optional/youtube-library/install")
        resp = await client.get("/api/apps/optional/catalog")
        app = next(a for a in resp.json()["apps"] if a["id"] == "youtube-library")
        assert app["installed"] is True
        assert app["update_available"] is False

    @pytest.mark.asyncio
    async def test_update_available_true_when_recorded_version_is_older(self, apps_client):
        """update_available flips true when the stored version is behind APP_VERSIONS."""
        from tinyagentos.routes.apps import APP_VERSIONS
        import json
        client, store = apps_client
        # Seed the DB with an older version directly.
        await store._db.execute(
            "INSERT OR REPLACE INTO installed_apps (app_id, installed_at, version, metadata) VALUES (?, ?, ?, ?)",
            ("x-monitor", 1000.0, "0.9.0", json.dumps({"kind": "frontend-app"})),
        )
        await store._db.commit()
        resp = await client.get("/api/apps/optional/catalog")
        app = next(a for a in resp.json()["apps"] if a["id"] == "x-monitor")
        assert app["installed"] is True
        # APP_VERSIONS["x-monitor"] is "1.0.0" which is > "0.9.0"
        assert app["update_available"] is True
        assert app["version"] == APP_VERSIONS["x-monitor"]

    @pytest.mark.asyncio
    async def test_catalog_does_not_leak_unknown_ids(self, apps_client):
        """Catalog must never return ids outside the allowlist."""
        from tinyagentos.routes.apps import OPTIONAL_FRONTEND_APPS
        import json
        client, store = apps_client
        # Inject a foreign row directly (bypassing the install endpoint).
        await store._db.execute(
            "INSERT OR REPLACE INTO installed_apps (app_id, installed_at, version, metadata) VALUES (?, ?, ?, ?)",
            ("totally-random-app", 1000.0, "1.0.0", json.dumps({"kind": "frontend-app"})),
        )
        await store._db.commit()
        resp = await client.get("/api/apps/optional/catalog")
        returned_ids = {a["id"] for a in resp.json()["apps"]}
        assert "totally-random-app" not in returned_ids
        assert returned_ids == OPTIONAL_FRONTEND_APPS
