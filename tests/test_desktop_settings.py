import pytest
import pytest_asyncio
from pathlib import Path
from tinyagentos.desktop_settings import DesktopSettingsStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = DesktopSettingsStore(tmp_path / "desktop.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_get_default_settings(store):
    settings = await store.get_settings("user")
    assert settings["mode"] == "dark"
    assert settings["wallpaper"] == "graphite"
    assert "pinned" in settings["dock"]


@pytest.mark.asyncio
async def test_update_settings(store):
    await store.update_settings("user", {"mode": "light"})
    settings = await store.get_settings("user")
    assert settings["mode"] == "light"


@pytest.mark.asyncio
async def test_get_dock_layout(store):
    dock = await store.get_dock("user")
    assert isinstance(dock["pinned"], list)
    assert "messages" in dock["pinned"]
    assert dock["iconSize"] == "medium"
    assert dock["position"] == "bottom"


@pytest.mark.asyncio
async def test_update_dock_layout(store):
    await store.update_dock("user", {"pinned": ["agents", "files"]})
    dock = await store.get_dock("user")
    assert dock["pinned"] == ["agents", "files"]


@pytest.mark.asyncio
async def test_update_dock_icon_size_and_position(store):
    await store.update_dock("user", {"iconSize": "large", "position": "left"})
    dock = await store.get_dock("user")
    assert dock["iconSize"] == "large"
    assert dock["position"] == "left"
    # Unrelated fields (e.g. pinned) survive the partial update.
    assert "messages" in dock["pinned"]


@pytest.mark.asyncio
async def test_window_positions_roundtrip(store):
    positions = [{"appId": "messages", "x": 100, "y": 200, "w": 900, "h": 600}]
    await store.save_windows("user", positions)
    result = await store.get_windows("user")
    assert result == positions


@pytest.mark.asyncio
async def test_widgets_roundtrip(store):
    widgets = [
        {"id": "w1", "type": "clock", "x": 0, "y": 0, "w": 4, "h": 3},
        {"id": "w2", "type": "agent-status", "x": 4, "y": 0, "w": 4, "h": 4},
    ]
    await store.save_widgets("user", widgets)
    result = await store.get_widgets("user")
    assert result == widgets


@pytest.mark.asyncio
async def test_widgets_default_empty(store):
    result = await store.get_widgets("user")
    assert result == []
