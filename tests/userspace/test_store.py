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
