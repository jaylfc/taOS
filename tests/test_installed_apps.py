import pytest
import pytest_asyncio

from tinyagentos.installed_apps import InstalledAppsStore


@pytest_asyncio.fixture
async def store(tmp_path):
    store = InstalledAppsStore(tmp_path / "installed_apps.db")
    await store.init()
    yield store
    await store.close()


@pytest.mark.asyncio
async def test_install_and_is_installed(store):
    assert not await store.is_installed("myapp")
    await store.install("myapp", version="1.0.0")
    assert await store.is_installed("myapp")


@pytest.mark.asyncio
async def test_install_default_version_and_metadata(store):
    await store.install("myapp")
    rows = await store.list_installed()
    assert len(rows) == 1
    assert rows[0]["app_id"] == "myapp"
    assert rows[0]["version"] == ""
    assert rows[0]["metadata"] == {}


@pytest.mark.asyncio
async def test_install_with_metadata(store):
    await store.install("myapp", version="2.0.0", metadata={"author": "test"})
    rows = await store.list_installed()
    assert rows[0]["version"] == "2.0.0"
    assert rows[0]["metadata"] == {"author": "test"}


@pytest.mark.asyncio
async def test_install_replace_existing(store):
    await store.install("myapp", version="1.0.0", metadata={"old": "data"})
    await store.install("myapp", version="2.0.0", metadata={"new": "data"})
    rows = await store.list_installed()
    assert len(rows) == 1
    assert rows[0]["version"] == "2.0.0"
    assert rows[0]["metadata"] == {"new": "data"}


@pytest.mark.asyncio
async def test_list_installed_order(store):
    await store.install("app-a", version="1.0")
    await store.install("app-b", version="1.0")
    await store.install("app-c", version="1.0")
    rows = await store.list_installed()
    assert [r["app_id"] for r in rows] == ["app-c", "app-b", "app-a"]


@pytest.mark.asyncio
async def test_list_installed_empty(store):
    rows = await store.list_installed()
    assert rows == []


@pytest.mark.asyncio
async def test_uninstall_returns_true_when_exists(store):
    await store.install("myapp")
    assert await store.uninstall("myapp") is True
    assert not await store.is_installed("myapp")


@pytest.mark.asyncio
async def test_uninstall_returns_false_when_missing(store):
    assert await store.uninstall("nonexistent") is False


@pytest.mark.asyncio
async def test_update_and_get_runtime_location(store):
    await store.update_runtime_location("myapp", "localhost", 8080, backend="rkllama", ui_path="/ui")
    loc = await store.get_runtime_location("myapp")
    assert loc is not None
    assert loc["runtime_host"] == "localhost"
    assert loc["runtime_port"] == 8080
    assert loc["backend"] == "rkllama"
    assert loc["ui_path"] == "/ui"


@pytest.mark.asyncio
async def test_get_runtime_location_returns_none_when_missing(store):
    assert await store.get_runtime_location("nonexistent") is None


@pytest.mark.asyncio
async def test_update_runtime_location_defaults(store):
    await store.update_runtime_location("myapp", "host", 9000)
    loc = await store.get_runtime_location("myapp")
    assert loc["backend"] == ""
    assert loc["ui_path"] == "/"


@pytest.mark.asyncio
async def test_update_runtime_location_overwrite(store):
    await store.update_runtime_location("myapp", "host1", 1000)
    await store.update_runtime_location("myapp", "host2", 2000, backend="b2", ui_path="/p")
    loc = await store.get_runtime_location("myapp")
    assert loc["runtime_host"] == "host2"
    assert loc["runtime_port"] == 2000
    assert loc["backend"] == "b2"
    assert loc["ui_path"] == "/p"


@pytest.mark.asyncio
async def test_remove_runtime_location(store):
    await store.update_runtime_location("myapp", "host", 8080)
    await store.remove_runtime_location("myapp")
    assert await store.get_runtime_location("myapp") is None


@pytest.mark.asyncio
async def test_remove_runtime_location_when_missing(store):
    await store.remove_runtime_location("nonexistent")


@pytest.mark.asyncio
async def test_full_round_trip(store):
    await store.install("myapp", version="1.0.0", metadata={"key": "val"})
    assert await store.is_installed("myapp")

    rows = await store.list_installed()
    assert len(rows) == 1
    assert rows[0]["app_id"] == "myapp"

    await store.update_runtime_location("myapp", "127.0.0.1", 3000)
    loc = await store.get_runtime_location("myapp")
    assert loc["runtime_host"] == "127.0.0.1"
    assert loc["runtime_port"] == 3000

    assert await store.uninstall("myapp") is True
    assert not await store.is_installed("myapp")
    assert await store.get_runtime_location("myapp") is not None


@pytest.mark.asyncio
async def test_multiple_apps(store):
    await store.install("app-1", version="1.0")
    await store.install("app-2", version="2.0")
    await store.install("app-3", version="3.0")

    rows = await store.list_installed()
    assert len(rows) == 3

    assert await store.is_installed("app-1")
    assert await store.is_installed("app-2")
    assert await store.is_installed("app-3")

    await store.uninstall("app-2")
    assert not await store.is_installed("app-2")
    assert await store.is_installed("app-1")
    assert await store.is_installed("app-3")

    rows = await store.list_installed()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_office_suite_install_row_migrated_to_office_studio(tmp_path):
    # A user who installed the app under its old id keeps it installed after
    # the Office Suite -> Office Studio rename.
    db = tmp_path / "installed_apps.db"
    first = InstalledAppsStore(db)
    await first.init()
    await first.install("office-suite", version="1.0.0")
    await first.update_runtime_location("office-suite", host="127.0.0.1", port=1234)
    await first.close()

    # Re-opening runs _post_init, which performs the rename migration.
    second = InstalledAppsStore(db)
    await second.init()
    try:
        assert not await second.is_installed("office-suite")
        assert await second.is_installed("office-studio")
        runtime = await second.get_runtime_location("office-studio")
        assert runtime is not None and runtime["runtime_port"] == 1234
    finally:
        await second.close()


@pytest.mark.asyncio
async def test_rename_migration_preserves_newer_target_row(tmp_path):
    # If both the old and new ids somehow exist, the migration must keep the
    # newer office-studio row and just drop the stale office-suite row, never
    # overwrite the newer data.
    db = tmp_path / "installed_apps.db"
    first = InstalledAppsStore(db)
    await first.init()
    await first.install("office-suite", version="old")
    await first.install("office-studio", version="new")
    await first.close()

    second = InstalledAppsStore(db)
    await second.init()
    try:
        assert not await second.is_installed("office-suite")
        rows = {r["app_id"]: r for r in await second.list_installed()}
        assert "office-studio" in rows
        assert rows["office-studio"]["version"] == "new"
    finally:
        await second.close()
