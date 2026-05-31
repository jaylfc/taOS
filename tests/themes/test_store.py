import json, pytest, pytest_asyncio
from tinyagentos.themes.store import ThemeStore

@pytest_asyncio.fixture
async def store(tmp_path):
    s = ThemeStore(tmp_path / "themes.sqlite3")
    await s.init()
    yield s
    await s.close()

@pytest.mark.asyncio
async def test_install_list_get_remove(store):
    cfg = {"tokens": {"--color-accent": "#00ff46"}, "structure": {}, "effects": [], "requires": ["assistant", "launcher"]}
    await store.install(theme_id="matrix", name="Matrix", version="1.0.0", config=cfg)
    rows = await store.list_installed()
    assert any(r["theme_id"] == "matrix" for r in rows)
    got = await store.get("matrix")
    assert got["config"]["tokens"]["--color-accent"] == "#00ff46"
    assert await store.remove("matrix") is True
    assert await store.get("matrix") is None
