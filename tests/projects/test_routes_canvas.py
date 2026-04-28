import asyncio
import json

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient
from httpx import ASGITransport

from tinyagentos.projects.events import ProjectEventBroker
from tinyagentos.projects.project_store import ProjectStore
from tinyagentos.projects.canvas.store import ProjectCanvasStore
from tinyagentos.projects.canvas.snapshotter import CanvasSnapshotter
from tinyagentos.projects.folders import ensure_project_layout
from tinyagentos.routes.project_canvas import router as canvas_router


@pytest_asyncio.fixture
async def client(tmp_path):
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

    app = FastAPI()
    app.state.project_store = ps
    app.state.project_canvas_store = cs
    app.state.canvas_snapshotter = snap
    app.state.project_broker = broker
    app.state.projects_root = data_root
    app.include_router(canvas_router)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, p, app
    await snap.stop()
    await cs.close()
    await ps.close()


@pytest.mark.asyncio
async def test_get_elements_empty(client):
    c, p, _ = client
    r = await c.get(f"/api/projects/{p['id']}/canvas/elements")
    assert r.status_code == 200
    assert r.json() == {"elements": []}


@pytest.mark.asyncio
async def test_post_then_get_elements(client):
    c, p, _ = client
    body = {"kind": "note", "x": 1, "y": 2, "w": 100, "h": 50,
            "payload": {"text": "hello", "color": "yellow", "font_size": 14}}
    r = await c.post(f"/api/projects/{p['id']}/canvas/elements", json=body)
    assert r.status_code == 201, r.text
    elem = r.json()["element"]
    assert elem["kind"] == "note"
    assert elem["payload"]["text"] == "hello"

    r2 = await c.get(f"/api/projects/{p['id']}/canvas/elements")
    assert len(r2.json()["elements"]) == 1
