import asyncio
import json
from pathlib import Path

import pytest
import pytest_asyncio

from tinyagentos.projects.events import ProjectEventBroker
from tinyagentos.projects.project_store import ProjectStore
from tinyagentos.projects.canvas.store import ProjectCanvasStore
from tinyagentos.projects.canvas.snapshotter import CanvasSnapshotter
from tinyagentos.projects.folders import ensure_project_layout


@pytest_asyncio.fixture
async def env(tmp_path):
    db = tmp_path / "db.sqlite"
    data_root = tmp_path / "data"
    broker = ProjectEventBroker()
    ps = ProjectStore(db); await ps.init()
    cs = ProjectCanvasStore(db, broker=broker); await cs.init()
    p = await ps.create_project(name="Alpha", slug="alpha", created_by="u")
    ensure_project_layout(data_root, p["slug"], p["name"])
    snap = CanvasSnapshotter(
        project_store=ps, canvas_store=cs, broker=broker,
        data_root=data_root, debounce_seconds=0.05,
    )
    await snap.start()
    yield ps, cs, snap, p, data_root
    await snap.stop()
    await cs.close()
    await ps.close()


@pytest.mark.asyncio
async def test_add_element_writes_tldr_within_debounce(env):
    ps, cs, snap, p, data_root = env
    # Ensure subscribed before the publish so the dirty flag is set
    await snap._ensure_subscribed(p["id"])
    await cs.add_element(
        project_id=p["id"], author_kind="user", author_id="u",
        element={"kind": "note", "x": 0, "y": 0, "w": 100, "h": 50,
                 "payload": {"text": "hi", "color": "yellow", "font_size": 14}},
    )
    target = data_root / p["slug"] / "canvas" / "board.tldr"
    for _ in range(20):
        if target.exists():
            break
        await asyncio.sleep(0.05)
    assert target.exists()
    body = json.loads(target.read_text())
    assert "store" in body
    shape_keys = [k for k in body["store"].keys() if k.startswith("shape:")]
    assert len(shape_keys) == 1


@pytest.mark.asyncio
async def test_export_now_synchronous(env):
    ps, cs, snap, p, data_root = env
    await cs.add_element(
        project_id=p["id"], author_kind="user", author_id="u",
        element={"kind": "note", "x": 1, "y": 1, "w": 10, "h": 10,
                 "payload": {"text": "x"}},
    )
    path = await snap.export_now(p["id"])
    assert path is not None
    assert path.exists()
