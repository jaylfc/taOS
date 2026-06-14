import pytest
from tinyagentos.userspace.store import UserspaceAppStore


@pytest.mark.asyncio
async def test_install_list_and_uninstall(tmp_path):
    store = UserspaceAppStore(tmp_path / "userspace_apps.db")
    await store.init()
    await store.install(
        app_id="todo", name="Todo", version="1.0.0", app_type="web",
        entry="index.html", icon="icon.png", permissions_requested=["app.net"],
    )
    rows = await store.list_installed()
    assert len(rows) == 1
    assert rows[0]["app_id"] == "todo"
    assert rows[0]["app_type"] == "web"
    assert rows[0]["enabled"] == 1
    assert rows[0]["permissions_granted"] == []
    assert rows[0]["permissions_requested"] == ["app.net"]
    assert await store.uninstall("todo") is True
    assert await store.list_installed() == []
    await store.close()


@pytest.mark.asyncio
async def test_set_permissions_and_enabled(tmp_path):
    store = UserspaceAppStore(tmp_path / "u.db")
    await store.init()
    await store.install(app_id="a", name="A", version="1", app_type="web",
                        entry="index.html", icon="i.png",
                        permissions_requested=["app.net", "app.memory"])
    await store.set_permissions_granted("a", ["app.net"])
    await store.set_enabled("a", False)
    row = await store.get("a")
    assert row["permissions_granted"] == ["app.net"]
    assert row["enabled"] == 0
    await store.close()


@pytest.mark.asyncio
async def test_fresh_install_has_no_runtime_location(tmp_path):
    store = UserspaceAppStore(tmp_path / "rt.db")
    await store.init()
    await store.install(app_id="ctr", name="Ctr", version="1.0.0",
                        app_type="container", entry="index.html", icon="",
                        permissions_requested=[])
    row = await store.get("ctr")
    assert row["container_host"] is None
    assert row["container_port"] is None
    await store.close()


@pytest.mark.asyncio
async def test_set_runtime_location(tmp_path):
    store = UserspaceAppStore(tmp_path / "rt2.db")
    await store.init()
    await store.install(app_id="ctr", name="Ctr", version="1.0.0",
                        app_type="container", entry="index.html", icon="",
                        permissions_requested=[])
    await store.set_runtime_location("ctr", "127.0.0.1", 13042)
    row = await store.get("ctr")
    assert row["container_host"] == "127.0.0.1"
    assert row["container_port"] == 13042
    await store.close()


@pytest.mark.asyncio
async def test_reinstall_preserves_runtime_location(tmp_path):
    store = UserspaceAppStore(tmp_path / "rt3.db")
    await store.init()
    await store.install(app_id="ctr", name="Ctr", version="1.0.0",
                        app_type="container", entry="index.html", icon="",
                        permissions_requested=[])
    await store.set_runtime_location("ctr", "127.0.0.1", 13042)
    # Re-install (upsert) should not wipe the runtime location
    await store.install(app_id="ctr", name="Ctr v2", version="1.0.1",
                        app_type="container", entry="index.html", icon="",
                        permissions_requested=[])
    row = await store.get("ctr")
    assert row["container_host"] == "127.0.0.1"
    assert row["container_port"] == 13042
    assert row["version"] == "1.0.1"
    await store.close()
