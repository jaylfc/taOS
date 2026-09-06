import pytest
import pytest_asyncio

from tinyagentos.install_registry import InstallRegistryStore


class TestInstallRegistryStore:
    @pytest.mark.asyncio
    async def test_record_and_get(self, tmp_path):
        store = InstallRegistryStore(tmp_path / "test.db")
        await store.init()
        try:
            row = await store.record(
                item_id="com.example.app1",
                item_kind="app",
                version="1.0.0",
                location_kind="own_lxc",
                location_ref="container-abc",
                update_channel="stable",
            )
            assert row["item_id"] == "com.example.app1"
            assert row["item_kind"] == "app"
            assert row["version"] == "1.0.0"
            assert row["location_kind"] == "own_lxc"
            assert row["location_ref"] == "container-abc"
            assert row["update_channel"] == "stable"

            fetched = await store.get(row["id"])
            assert fetched is not None
            assert fetched["item_id"] == "com.example.app1"
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_record_upsert_dedupe(self, tmp_path):
        store = InstallRegistryStore(tmp_path / "test.db")
        await store.init()
        try:
            row1 = await store.record(
                item_id="com.example.app1",
                item_kind="app",
                version="1.0.0",
                location_kind="own_lxc",
                location_ref="container-abc",
            )
            row2 = await store.record(
                item_id="com.example.app1",
                item_kind="app",
                version="2.0.0",
                location_kind="own_lxc",
                location_ref="container-abc",
                update_channel="beta",
            )
            assert row1["id"] == row2["id"]
            assert row2["version"] == "2.0.0"
            assert row2["update_channel"] == "beta"

            all_rows = await store.list()
            assert len(all_rows) == 1
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_list_filters(self, tmp_path):
        store = InstallRegistryStore(tmp_path / "test.db")
        await store.init()
        try:
            r1 = await store.record("app-1", "app", "1.0", "own_lxc", "c1")
            r2 = await store.record("app-1", "app", "2.0", "host", "host")
            r3 = await store.record("svc-1", "service", "1.0", "stack", "my-stack")

            all_rows = await store.list()
            assert len(all_rows) == 3

            by_item = await store.list(item_id="app-1")
            assert len(by_item) == 2
            assert all(r["item_id"] == "app-1" for r in by_item)

            by_loc = await store.list(location_ref="c1")
            assert len(by_loc) == 1
            assert by_loc[0]["id"] == r1["id"]
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_set_version(self, tmp_path):
        store = InstallRegistryStore(tmp_path / "test.db")
        await store.init()
        try:
            row = await store.record("app-1", "app", "1.0", "host", "host")
            updated = await store.set_version(row["id"], "2.0.0")
            assert updated is not None
            assert updated["version"] == "2.0.0"
            assert updated["updated_at"] >= updated["installed_at"]
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_set_version_not_found(self, tmp_path):
        store = InstallRegistryStore(tmp_path / "test.db")
        await store.init()
        try:
            result = await store.set_version("ir-nosuch", "1.0")
            assert result is None
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_delete(self, tmp_path):
        store = InstallRegistryStore(tmp_path / "test.db")
        await store.init()
        try:
            row = await store.record("app-1", "app", "1.0", "host", "host")
            assert await store.delete(row["id"]) is True
            assert await store.get(row["id"]) is None
            assert await store.delete(row["id"]) is False
        finally:
            await store.close()

    @pytest.mark.asyncio
    async def test_get_not_found(self, tmp_path):
        store = InstallRegistryStore(tmp_path / "test.db")
        await store.init()
        try:
            assert await store.get("ir-nosuch") is None
        finally:
            await store.close()


@pytest_asyncio.fixture
async def install_registry_client(app, client):
    store = app.state.install_registry
    if store._db is not None:
        await store.close()
    await store.init()
    yield client, app


class TestInstallRegistryRoutes:
    @pytest.mark.asyncio
    async def test_record_and_list(self, install_registry_client):
        client, _app = install_registry_client
        r = await client.post("/api/installs", json={
            "item_id": "com.example.app1",
            "item_kind": "app",
            "version": "1.0.0",
            "location_kind": "own_lxc",
            "location_ref": "container-abc",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["item_id"] == "com.example.app1"
        assert data["version"] == "1.0.0"
        entry_id = data["id"]

        r = await client.get(f"/api/installs/{entry_id}")
        assert r.status_code == 200
        assert r.json()["id"] == entry_id

        r = await client.get("/api/installs")
        assert r.status_code == 200
        items = r.json()
        assert any(i["id"] == entry_id for i in items)

    @pytest.mark.asyncio
    async def test_upsert_dedupe_via_route(self, install_registry_client):
        client, _app = install_registry_client
        body = {
            "item_id": "com.example.app1",
            "item_kind": "app",
            "version": "1.0.0",
            "location_kind": "own_lxc",
            "location_ref": "container-abc",
        }
        r1 = await client.post("/api/installs", json=body)
        assert r1.status_code == 200
        id1 = r1.json()["id"]

        body["version"] = "2.0.0"
        r2 = await client.post("/api/installs", json=body)
        assert r2.status_code == 200
        id2 = r2.json()["id"]

        assert id1 == id2
        assert r2.json()["version"] == "2.0.0"

        r = await client.get("/api/installs")
        items = r.json()
        matching = [i for i in items if i["item_id"] == "com.example.app1"]
        assert len(matching) == 1

    @pytest.mark.asyncio
    async def test_list_with_filters(self, install_registry_client):
        client, _app = install_registry_client
        await client.post("/api/installs", json={
            "item_id": "app-1", "item_kind": "app",
            "version": "1.0", "location_kind": "own_lxc", "location_ref": "c1",
        })
        await client.post("/api/installs", json={
            "item_id": "app-1", "item_kind": "app",
            "version": "2.0", "location_kind": "host", "location_ref": "host",
        })

        r = await client.get("/api/installs", params={"item_id": "app-1"})
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 2

        r = await client.get("/api/installs", params={"location_ref": "c1"})
        assert r.status_code == 200
        items = r.json()
        assert len(items) == 1
        assert items[0]["location_ref"] == "c1"

    @pytest.mark.asyncio
    async def test_set_version_via_route(self, install_registry_client):
        client, _app = install_registry_client
        r = await client.post("/api/installs", json={
            "item_id": "app-1", "item_kind": "app",
            "version": "1.0", "location_kind": "host", "location_ref": "host",
        })
        entry_id = r.json()["id"]

        r = await client.patch(f"/api/installs/{entry_id}", json={"version": "2.0.0"})
        assert r.status_code == 200
        assert r.json()["version"] == "2.0.0"

    @pytest.mark.asyncio
    async def test_set_version_not_found(self, install_registry_client):
        client, _app = install_registry_client
        r = await client.patch("/api/installs/ir-nosuch", json={"version": "1.0"})
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_via_route(self, install_registry_client):
        client, _app = install_registry_client
        r = await client.post("/api/installs", json={
            "item_id": "app-1", "item_kind": "app",
            "version": "1.0", "location_kind": "host", "location_ref": "host",
        })
        entry_id = r.json()["id"]

        r = await client.delete(f"/api/installs/{entry_id}")
        assert r.status_code == 200

        r = await client.get(f"/api/installs/{entry_id}")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_not_found(self, install_registry_client):
        client, _app = install_registry_client
        r = await client.delete("/api/installs/ir-nosuch")
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_get_not_found(self, install_registry_client):
        client, _app = install_registry_client
        r = await client.get("/api/installs/ir-nosuch")
        assert r.status_code == 404
