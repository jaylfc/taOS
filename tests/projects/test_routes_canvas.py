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


@pytest.mark.asyncio
async def test_patch_element_updates_payload(client):
    c, p, _ = client
    r = await c.post(f"/api/projects/{p['id']}/canvas/elements", json={
        "kind": "note", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {"text": "a"}})
    eid = r.json()["element"]["id"]
    r2 = await c.patch(f"/api/projects/{p['id']}/canvas/elements/{eid}",
                        json={"x": 99, "payload": {"text": "edited"}})
    assert r2.status_code == 200
    assert r2.json()["element"]["x"] == 99
    assert r2.json()["element"]["payload"]["text"] == "edited"


@pytest.mark.asyncio
async def test_delete_element_returns_204_and_hides(client):
    c, p, _ = client
    r = await c.post(f"/api/projects/{p['id']}/canvas/elements", json={
        "kind": "note", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {"text": "a"}})
    eid = r.json()["element"]["id"]
    r2 = await c.delete(f"/api/projects/{p['id']}/canvas/elements/{eid}")
    assert r2.status_code == 204
    r3 = await c.get(f"/api/projects/{p['id']}/canvas/elements")
    assert r3.json()["elements"] == []


@pytest.mark.asyncio
async def test_snapshot_png_renders(client):
    c, p, _ = client
    await c.post(f"/api/projects/{p['id']}/canvas/elements", json={
        "kind": "note", "x": 0, "y": 0, "w": 100, "h": 50, "payload": {"text": "hi"}})
    r = await c.get(f"/api/projects/{p['id']}/canvas/snapshot.png")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert len(r.content) > 100


@pytest.mark.asyncio
async def test_permission_toggle(client):
    c, p, app = client
    ps = app.state.project_store
    await ps.add_member(p["id"], "agent-1", member_kind="native")
    r = await c.patch(
        f"/api/projects/{p['id']}/canvas/permissions/agent-1",
        json={"can_edit_canvas": True},
    )
    assert r.status_code == 200
    members = await ps.list_members(p["id"])
    me = next(m for m in members if m["member_id"] == "agent-1")
    assert me["can_edit_canvas"] == 1


async def _collect_canvas_sse(app, project_id, n_lines, timeout=5.0):
    """Drive the canvas SSE endpoint over raw ASGI and collect n_lines."""
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "headers": [(b"accept", b"text/event-stream")],
        "scheme": "http",
        "path": f"/api/projects/{project_id}/canvas/stream",
        "raw_path": f"/api/projects/{project_id}/canvas/stream".encode(),
        "query_string": b"",
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
        "root_path": "",
    }

    lines: list[str] = []
    done = asyncio.Event()

    async def receive():
        await done.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                for line in body.decode().split("\n"):
                    stripped = line.rstrip("\r")
                    if stripped:
                        lines.append(stripped)
                        if len(lines) >= n_lines:
                            done.set()

    task = asyncio.create_task(app(scope, receive, send))
    try:
        await asyncio.wait_for(asyncio.shield(done.wait()), timeout=timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    return lines


@pytest.mark.asyncio
async def test_sse_stream_emits_canvas_events(client):
    c, p, app = client

    # Publish a canvas event into the broker replay buffer first
    # so the SSE subscriber gets it immediately on subscribe.
    from tinyagentos.projects.events import ProjectEvent
    await app.state.project_broker.publish(
        p["id"],
        ProjectEvent(kind="canvas.element_added", payload={"id": "e1"}),
    )

    lines = await _collect_canvas_sse(app, p["id"], n_lines=1, timeout=3.0)
    data_lines = [l for l in lines if l.startswith("data:")]
    assert data_lines, f"No data: lines received; got: {lines}"
    evt = json.loads(data_lines[0][5:].strip())
    assert evt["type"] == "canvas.element_added"
