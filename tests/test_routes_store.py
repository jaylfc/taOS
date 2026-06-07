import pytest
import pytest_asyncio
import yaml
from httpx import AsyncClient, ASGITransport
from tinyagentos.app import create_app


@pytest.fixture
def catalog_dir(tmp_path):
    agents = tmp_path / "catalog" / "agents" / "smolagents"
    agents.mkdir(parents=True)
    (agents / "manifest.yaml").write_text(yaml.dump({
        "id": "smolagents", "name": "SmolAgents", "type": "agent-framework",
        "version": "1.0.0", "description": "Code-based agents",
        "requires": {"ram_mb": 256},
        "install": {"method": "pip", "package": "smolagents"},
        "hardware_tiers": {"arm-npu-16gb": "full", "cpu-only": "full"},
    }))
    models = tmp_path / "catalog" / "models" / "test-model"
    models.mkdir(parents=True)
    (models / "manifest.yaml").write_text(yaml.dump({
        "id": "test-model", "name": "Test Model", "type": "model",
        "version": "1.0.0", "description": "A test model",
        "variants": [{"id": "small", "name": "Small", "format": "gguf", "size_mb": 100,
                       "min_ram_mb": 512, "download_url": "https://example.com/test.gguf",
                       "backend": ["ollama"]}],
        "hardware_tiers": {"arm-npu-16gb": {"recommended": "small"}},
    }))
    return tmp_path / "catalog"


@pytest.fixture
def app_with_store(tmp_data_dir, catalog_dir):
    return create_app(data_dir=tmp_data_dir, catalog_dir=catalog_dir)


@pytest_asyncio.fixture
async def store_client(app_with_store):
    store = app_with_store.state.metrics
    if store._db is not None:
        await store.close()
    await store.init()
    await app_with_store.state.qmd_client.init()
    app_with_store.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    _rec = app_with_store.state.auth.find_user("admin")
    _token = app_with_store.state.auth.create_session(user_id=_rec["id"] if _rec else "", long_lived=True)
    transport = ASGITransport(app=app_with_store)
    async with AsyncClient(transport=transport, base_url="http://test", cookies={"taos_session": _token}) as c:
        yield c
    await store.close()
    await app_with_store.state.qmd_client.close()
    await app_with_store.state.http_client.aclose()


@pytest.mark.asyncio
class TestStoreAPI:
    async def test_list_catalog(self, store_client):
        resp = await store_client.get("/api/store/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        ids = {a["id"] for a in data}
        assert "smolagents" in ids
        assert "test-model" in ids
        # Every row carries a category field (empty string when unset) so the
        # frontend can group by category with a type fallback.
        assert all("category" in a for a in data)

    async def test_filter_catalog_by_type(self, store_client):
        resp = await store_client.get("/api/store/catalog?type=model")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["id"] == "test-model"

    async def test_list_installed_empty(self, store_client):
        resp = await store_client.get("/api/store/installed")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_get_app_detail(self, store_client):
        resp = await store_client.get("/api/store/app/smolagents")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "smolagents"
        assert data["type"] == "agent-framework"

    async def test_get_nonexistent_app(self, store_client):
        resp = await store_client.get("/api/store/app/nonexistent")
        assert resp.status_code == 404

    async def test_hardware_profile(self, store_client):
        resp = await store_client.get("/api/hardware")
        assert resp.status_code == 200
        data = resp.json()
        assert "profile_id" in data
        assert "ram_mb" in data
        assert data["ram_mb"] >= 0

    async def test_resolve_returns_200_not_500(self, store_client):
        """Regression: registry.get_app -> registry.get typo caused 500 on every resolve."""
        resp = await store_client.post(
            "/api/store/resolve", json={"app_id": "test-model"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "result" in data
        assert data["result"] in ("ok", "err")
        assert "compat" in data

    async def test_resolve_unknown_manifest_returns_404(self, store_client):
        resp = await store_client.post(
            "/api/store/resolve", json={"app_id": "does-not-exist"}
        )
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestCategoryPassthrough:
    @pytest.fixture
    def catalog_dir_with_categories(self, tmp_path):
        # A service manifest that uses category: to route itself out of the
        # Services bucket — matches the pattern shipped under app-catalog/services.
        svc = tmp_path / "catalog" / "services" / "gitea"
        svc.mkdir(parents=True)
        (svc / "manifest.yaml").write_text(yaml.dump({
            "id": "gitea", "name": "Gitea", "type": "service",
            "category": "dev-tool",
            "version": "1.22.0", "description": "Self-hosted Git server",
            "install": {"method": "docker", "image": "gitea/gitea:1.22"},
        }))
        return tmp_path / "catalog"

    @pytest_asyncio.fixture
    async def category_client(self, tmp_data_dir, catalog_dir_with_categories):
        app = create_app(data_dir=tmp_data_dir, catalog_dir=catalog_dir_with_categories)
        store = app.state.metrics
        if store._db is not None:
            await store.close()
        await store.init()
        await app.state.qmd_client.init()
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
        _rec = app.state.auth.find_user("admin")
        _token = app.state.auth.create_session(user_id=_rec["id"] if _rec else "", long_lived=True)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", cookies={"taos_session": _token}) as c:
            yield c
        await store.close()
        await app.state.qmd_client.close()
        await app.state.http_client.aclose()

    async def test_category_surfaces_in_catalog_response(self, category_client):
        resp = await category_client.get("/api/store/catalog")
        assert resp.status_code == 200
        rows = resp.json()
        gitea = next(a for a in rows if a["id"] == "gitea")
        # type stays "service" so lifecycle wiring (config.py, installation_state
        # etc.) is unchanged; category surfaces for UI grouping.
        assert gitea["type"] == "service"
        assert gitea["category"] == "dev-tool"


@pytest.mark.asyncio
class TestStoreInstallAPI:
    async def test_install_unknown_app_fails(self, store_client):
        resp = await store_client.post("/api/store/install", json={"app_id": "nonexistent"})
        assert resp.status_code == 404

    async def test_uninstall_not_installed_fails(self, store_client):
        resp = await store_client.post("/api/store/uninstall", json={"app_id": "smolagents"})
        assert resp.status_code == 404


@pytest.mark.asyncio
class TestInstallV2PersistsAcrossReload:
    """install-v2 must update the registry so /api/store/catalog reports the app
    as installed after a page reload — otherwise the Install button reverts
    (issue #317)."""

    async def test_install_v2_default_backend_marks_registry_installed(
        self, app_with_store, store_client
    ):
        await app_with_store.state.installed_apps.init()
        # smolagents has install.method == "pip" → default branch.
        resp = await store_client.post(
            "/api/store/install-v2", json={"app_id": "smolagents"}
        )
        assert resp.status_code == 200

        catalog = await store_client.get("/api/store/catalog")
        assert catalog.status_code == 200
        rows = catalog.json()
        smol = next(a for a in rows if a["id"] == "smolagents")
        assert smol["installed"] is True

    async def test_uninstall_v2_default_backend_marks_registry_uninstalled(
        self, app_with_store, store_client
    ):
        await app_with_store.state.installed_apps.init()
        await store_client.post("/api/store/install-v2", json={"app_id": "smolagents"})
        resp = await store_client.post(
            "/api/store/uninstall-v2", json={"app_id": "smolagents"}
        )
        assert resp.status_code == 200

        catalog = await store_client.get("/api/store/catalog")
        rows = catalog.json()
        smol = next(a for a in rows if a["id"] == "smolagents")
        assert smol["installed"] is False

    async def test_install_v2_default_backend_records_target_remote(
        self, app_with_store, store_client, monkeypatch
    ):
        """When target_remote is provided to a default-backend install, the
        runtime location is recorded against that remote so /installed-v2
        reports the right host."""
        # Patch remote_list so the registered-remote validation passes.
        async def _fake_remote_list():
            return [{"name": "orange-pi", "addr": "https://192.168.1.10:8443", "protocol": "incus"}]

        import tinyagentos.containers as containers
        monkeypatch.setattr(containers, "remote_list", _fake_remote_list)

        await app_with_store.state.installed_apps.init()
        resp = await store_client.post(
            "/api/store/install-v2",
            json={"app_id": "smolagents", "target_remote": "orange-pi"},
        )
        assert resp.status_code == 200

        listed = await store_client.get("/api/store/installed-v2")
        rows = listed.json()["installed"]
        smol = next(r for r in rows if r["app_id"] == "smolagents")
        # _resolve_host parses the registered remote's URL; for
        # https://192.168.1.10:8443 it returns "192.168.1.10".
        assert smol["runtime_host"] == "192.168.1.10"


@pytest.mark.asyncio
class TestInstallV2UpdatesRuntimeLocation:
    """After a successful LXC install, update_runtime_location should be called."""

    async def test_update_runtime_location_called_on_lxc_install(
        self, app_with_store, store_client
    ):
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_result = {
            "success": True,
            "app_id": "gitea-lxc",
            "backend": "lxc",
            "container": "taos-svc-gitea-lxc",
            "host_port": 13000,
            "gitea_version": "1.22.6",
            "admin_username": "admin",
        }
        update_calls: list = []

        async def fake_update(app_id, host, port, backend="", ui_path="/"):
            update_calls.append((app_id, host, port, backend, ui_path))

        mock_manifest = MagicMock()
        mock_manifest.install = {
            "method": "lxc",
            "image": "images:debian/bookworm",
            "ui_path": "/",
        }
        mock_manifest.version = "1.22.6"

        # Replace the installed_apps store with a mock so we can track calls
        # without needing the DB initialised in this fixture context.
        mock_store = MagicMock()
        mock_store.install = AsyncMock()
        mock_store.update_runtime_location = AsyncMock(side_effect=fake_update)
        app_with_store.state.installed_apps = mock_store

        with (
            patch(
                "tinyagentos.routes.store_install.LXCInstaller"
            ) as MockInstaller,
            patch("tinyagentos.registry.AppRegistry.get", return_value=mock_manifest),
        ):
            instance = MockInstaller.return_value
            instance.install = AsyncMock(return_value=mock_result)

            resp = await store_client.post("/api/store/install-v2", json={
                "app_id": "gitea-lxc",
                "admin_password": "secret",
            })

        assert resp.status_code == 200
        assert len(update_calls) == 1
        app_id, host, port, backend, ui_path = update_calls[0]
        assert app_id == "gitea-lxc"
        assert host == "127.0.0.1"  # local install → loopback
        assert port == 13000
        assert backend == "lxc"


@pytest.mark.asyncio
class TestDockerInstallRecordsRuntimeLocation:
    """Local docker services (e.g. SearxNG) must record a runtime location so
    they appear in /api/apps/installed and get a Launchpad shortcut.

    Regression: the docker branch previously fell through to a block that only
    recorded a location for remote installs (and with port=0), so a local
    docker install succeeded but never surfaced a shortcut.
    """

    @pytest.fixture
    def docker_catalog_dir(self, tmp_path):
        svc = tmp_path / "catalog" / "services" / "searxng"
        svc.mkdir(parents=True)
        (svc / "manifest.yaml").write_text(yaml.dump({
            "id": "searxng", "name": "SearXNG", "type": "service",
            "category": "infrastructure", "version": "2024.12.0",
            "description": "Privacy-respecting metasearch engine",
            "requires": {"ram_mb": 128, "ports": [8080]},
            "install": {
                "method": "docker",
                "image": "searxng/searxng:latest",
                "ports": [8080],
            },
        }))
        return tmp_path / "catalog"

    @pytest_asyncio.fixture
    async def docker_client(self, tmp_data_dir, docker_catalog_dir):
        app = create_app(data_dir=tmp_data_dir, catalog_dir=docker_catalog_dir)
        store = app.state.metrics
        if store._db is not None:
            await store.close()
        await store.init()
        await app.state.qmd_client.init()
        await app.state.installed_apps.init()
        app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
        _rec = app.state.auth.find_user("admin")
        _token = app.state.auth.create_session(user_id=_rec["id"] if _rec else "", long_lived=True)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test", cookies={"taos_session": _token}) as c:
            yield c, app
        await store.close()
        await app.state.qmd_client.close()
        await app.state.http_client.aclose()

    async def test_local_docker_install_records_runtime_location_and_appears(
        self, docker_client
    ):
        from unittest.mock import AsyncMock, patch

        client, app = docker_client

        # Mock DockerInstaller so no real docker/compose runs; both pull and
        # the auto-start succeed.
        with patch(
            "tinyagentos.installers.docker_installer.DockerInstaller"
        ) as MockDocker:
            instance = MockDocker.return_value
            instance.install = AsyncMock(return_value={"success": True, "path": "/tmp/x"})
            instance.start = AsyncMock(return_value={"success": True, "output": ""})

            resp = await client.post(
                "/api/store/install-v2", json={"app_id": "searxng"}
            )
        assert resp.status_code == 200

        # Runtime location recorded with the declared host port + docker backend.
        loc = await app.state.installed_apps.get_runtime_location("searxng")
        assert loc is not None
        assert loc["runtime_host"] == "127.0.0.1"
        assert loc["runtime_port"] == 8080
        assert loc["backend"] == "docker"

        # And it surfaces in /api/apps/installed so the Launchpad shows a shortcut.
        listed = await client.get("/api/apps/installed")
        assert listed.status_code == 200
        item = next(i for i in listed.json() if i["app_id"] == "searxng")
        assert item["url"] == "/apps/searxng/"
        assert item["status"] == "running"

    @pytest.mark.asyncio
    async def test_docker_compose_up_failure_returns_error_not_success(
        self, docker_client
    ):
        """When docker pull succeeds but compose up fails, the install must
        return a failure response — not silently mark the app as installed."""
        from unittest.mock import AsyncMock, patch

        client, app = docker_client

        with patch(
            "tinyagentos.installers.docker_installer.DockerInstaller"
        ) as MockDocker:
            instance = MockDocker.return_value
            instance.install = AsyncMock(return_value={"success": True, "path": "/tmp/x"})
            instance.start = AsyncMock(
                return_value={"success": False, "output": "port 8080 already in use"}
            )

            resp = await client.post(
                "/api/store/install-v2", json={"app_id": "searxng"}
            )

        assert resp.status_code == 500
        body = resp.json()
        assert body.get("ok") is False
        assert "container failed to start" in body.get("error", "").lower()
        assert "port" in body.get("error", "").lower() or "conflict" in body.get("error", "").lower() or "check logs" in body.get("error", "").lower()

        # Must NOT have been recorded as installed.
        loc = await app.state.installed_apps.get_runtime_location("searxng")
        assert loc is None

    @pytest.mark.asyncio
    async def test_docker_compose_up_raises_returns_error_not_success(
        self, docker_client
    ):
        """When compose up raises an exception, the install must return a
        failure — not silently mark the app as installed."""
        from unittest.mock import AsyncMock, patch

        client, app = docker_client

        with patch(
            "tinyagentos.installers.docker_installer.DockerInstaller"
        ) as MockDocker:
            instance = MockDocker.return_value
            instance.install = AsyncMock(return_value={"success": True, "path": "/tmp/x"})
            instance.start = AsyncMock(side_effect=RuntimeError("daemon not running"))

            resp = await client.post(
                "/api/store/install-v2", json={"app_id": "searxng"}
            )

        assert resp.status_code == 500
        body = resp.json()
        assert body.get("ok") is False
        assert "daemon not running" in body.get("error", "")

        loc = await app.state.installed_apps.get_runtime_location("searxng")
        assert loc is None


