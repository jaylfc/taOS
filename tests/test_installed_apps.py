import pytest
import pytest_asyncio

from tinyagentos.installed_apps import InstalledAppsStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = InstalledAppsStore(tmp_path / "installed.db")
    await s.init()
    yield s
    await s.close()


# --- init / _post_init app-id rename migration ---

@pytest.mark.asyncio
async def test_post_init_renames_old_app_id_to_new(tmp_path):
    s = InstalledAppsStore(tmp_path / "renamed.db")
    db = s._db = await __import__("aiosqlite").connect(":memory:")
    await db.executescript(InstalledAppsStore.SCHEMA)
    await db.commit()
    await db.execute("INSERT INTO installed_apps (app_id, installed_at) VALUES (?, ?)", ("office-suite", 1.0))
    await db.execute("INSERT INTO app_runtime (app_id, runtime_host, runtime_port) VALUES (?, ?, ?)", ("office-suite", "h", 1))
    await db.commit()
    s._db = db
    await s._post_init()
    row = await (await db.execute("SELECT app_id FROM installed_apps WHERE app_id = ?", ("office-studio",))).fetchone()
    assert row is not None
    row = await (await db.execute("SELECT app_id FROM app_runtime WHERE app_id = ?", ("office-studio",))).fetchone()
    assert row is not None
    await db.close()


@pytest.mark.asyncio
async def test_post_init_keeps_new_id_when_both_exist(tmp_path):
    s = InstalledAppsStore(tmp_path / "both.db")
    db = s._db = await __import__("aiosqlite").connect(":memory:")
    await db.executescript(InstalledAppsStore.SCHEMA)
    await db.commit()
    await db.execute("INSERT INTO installed_apps (app_id, installed_at, version) VALUES (?, ?, ?)", ("office-studio", 2.0, "2"))
    await db.execute("INSERT INTO installed_apps (app_id, installed_at, version) VALUES (?, ?, ?)", ("office-suite", 1.0, "1"))
    await db.commit()
    s._db = db
    await s._post_init()
    rows = await (await db.execute("SELECT app_id, version, installed_at FROM installed_apps ORDER BY app_id")).fetchall()
    assert len(rows) == 1
    assert rows[0] == ("office-studio", "2", 2.0)
    await db.close()


@pytest.mark.asyncio
async def test_post_init_is_noop_when_no_renames_pending(tmp_path):
    s = InstalledAppsStore(tmp_path / "clean.db")
    await s.init()
    assert await s.is_installed("otherapp") is False
    await s.close()


# --- install ---

@pytest.mark.asyncio
async def test_install_creates_row(store):
    await store.install("myapp", version="1.2.3", metadata={"author": "x"})
    assert await store.is_installed("myapp") is True


@pytest.mark.asyncio
async def test_install_defaults_version_and_metadata(store):
    await store.install("myapp")
    apps = await store.list_installed()
    assert apps[0]["version"] == ""
    assert apps[0]["metadata"] == {}


@pytest.mark.asyncio
async def test_install_replace_updates_version(store):
    await store.install("myapp", version="1.0")
    v1_ts = (await store.list_installed())[0]["installed_at"]
    await store.install("myapp", version="2.0")
    apps = await store.list_installed()
    assert apps[0]["version"] == "2.0"
    assert apps[0]["installed_at"] > v1_ts


@pytest.mark.asyncio
async def test_install_metadata_roundtrip(store):
    await store.install("myapp", metadata={"k": "v", "n": 1})
    apps = await store.list_installed()
    assert apps[0]["metadata"] == {"k": "v", "n": 1}


# --- list_installed ---

@pytest.mark.asyncio
async def test_list_installed_ordered_by_installed_at_desc(store):
    await store.install("b", version="1")
    await store.install("a", version="2")
    await store.install("c", version="3")
    ids = [a["app_id"] for a in await store.list_installed()]
    assert ids == ["c", "a", "b"]


@pytest.mark.asyncio
async def test_list_installed_empty_initially(store):
    assert await store.list_installed() == []


# --- is_installed / uninstall ---

@pytest.mark.asyncio
async def test_uninstall_removes_row_and_returns_true(store):
    await store.install("myapp")
    assert await store.uninstall("myapp") is True
    assert await store.is_installed("myapp") is False


@pytest.mark.asyncio
async def test_uninstall_missing_returns_false(store):
    assert await store.uninstall("nonexistent") is False


@pytest.mark.asyncio
async def test_is_installed_false_for_missing(store):
    assert await store.is_installed("ghost") is False


# --- runtime location ---

@pytest.mark.asyncio
async def test_update_and_get_runtime_location(store):
    await store.install("myapp")
    await store.update_runtime_location("myapp", "localhost", 8080, backend="fastapi", ui_path="/app")
    loc = await store.get_runtime_location("myapp")
    assert loc == {
        "runtime_host": "localhost",
        "runtime_port": 8080,
        "backend": "fastapi",
        "ui_path": "/app",
    }


@pytest.mark.asyncio
async def test_get_runtime_location_none_when_missing(store):
    assert await store.get_runtime_location("missing") is None


@pytest.mark.asyncio
async def test_update_runtime_location_replace(store):
    await store.install("myapp")
    await store.update_runtime_location("myapp", "a", 1)
    await store.update_runtime_location("myapp", "b", 2, ui_path="/x")
    loc = await store.get_runtime_location("myapp")
    assert loc == {"runtime_host": "b", "runtime_port": 2, "backend": "", "ui_path": "/x"}


@pytest.mark.asyncio
async def test_remove_runtime_location(store):
    await store.install("myapp")
    await store.update_runtime_location("myapp", "h", 1)
    await store.remove_runtime_location("myapp")
    assert await store.get_runtime_location("myapp") is None


@pytest.mark.asyncio
async def test_remove_runtime_location_missing_is_noop(store):
    await store.install("myapp")
    await store.remove_runtime_location("myapp")
    assert await store.is_installed("myapp") is True
