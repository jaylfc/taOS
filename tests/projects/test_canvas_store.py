import pytest
import pytest_asyncio

from tinyagentos.projects.canvas.store import ProjectCanvasStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ProjectCanvasStore(tmp_path / "canvas.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_store_init_creates_table(store):
    async with store._db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='project_canvas_elements'"
    ) as cur:
        row = await cur.fetchone()
    assert row is not None
