import asyncio
import json
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from tinyagentos.projects.events import ProjectEventBroker
from tinyagentos.projects.project_store import ProjectStore
from tinyagentos.projects.canvas.store import ProjectCanvasStore
from tinyagentos.projects.canvas.snapshotter import CanvasSnapshotter
from tinyagentos.projects.folders import ensure_project_layout
from tinyagentos.routes.project_canvas import router as canvas_router


@pytest_asyncio.fixture
async def app_env(tmp_path):
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
    await snap._ensure_subscribed(p["id"])

    app = FastAPI()
    app.state.project_store = ps
    app.state.project_canvas_store = cs
    app.state.canvas_snapshotter = snap
    app.state.project_event_broker = broker
    app.state.projects_root = data_root
    app.include_router(canvas_router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c, ps, p, data_root, app
    await snap.stop()
    await cs.close()
    await ps.close()


async def _collect_canvas_sse(app, project_id, *, stop_on: str, timeout: float = 3.0) -> list[str]:
    """Drive canvas SSE endpoint over raw ASGI; stop when stop_on appears in a line."""
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
                        if stop_on in stripped:
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
async def test_post_then_snapshot_then_sse(app_env):
    c, _, p, data_root, app = app_env

    # Post an element after a brief delay so SSE is already listening
    async def post_after():
        await asyncio.sleep(0.1)
        await c.post(f"/api/projects/{p['id']}/canvas/elements", json={
            "kind": "note", "x": 0, "y": 0, "w": 100, "h": 50, "payload": {"text": "live"}})

    poster = asyncio.create_task(post_after())
    lines = await _collect_canvas_sse(
        app, p["id"], stop_on="canvas.element_added", timeout=3.0
    )
    await poster

    data_lines = [l for l in lines if l.startswith("data:")]
    assert any("canvas.element_added" in l for l in data_lines), (
        f"canvas.element_added not found; lines: {lines}"
    )

    # snapshot file gets written within debounce
    target = data_root / p["slug"] / "canvas" / "board.tldr"
    for _ in range(20):
        if target.exists():
            break
        await asyncio.sleep(0.05)
    assert target.exists(), f"snapshot not written to {target}"
    body = json.loads(target.read_text())
    assert any(k.startswith("shape:") for k in body["store"].keys())


@pytest.mark.asyncio
async def test_permission_matrix(app_env):
    from tinyagentos.projects.canvas.store import CanvasPermissionError
    c, ps, p, _, app = app_env
    cs = app.state.project_canvas_store
    await ps.add_member(p["id"], "agent-1", member_kind="native")

    el = await cs.add_element(
        project_id=p["id"], author_kind="user", author_id="u",
        element={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {"text": "x"}},
    )
    # Default: agent cannot update
    with pytest.raises(CanvasPermissionError):
        await cs.update_element(
            project_id=p["id"], element_id=el["id"], patch={"x": 9},
            author_kind="agent", author_id="agent-1",
        )

    # Toggle on via REST
    r = await c.patch(
        f"/api/projects/{p['id']}/canvas/permissions/agent-1",
        json={"can_edit_canvas": True},
    )
    assert r.status_code == 200

    # Now agent can update
    updated = await cs.update_element(
        project_id=p["id"], element_id=el["id"], patch={"x": 9},
        author_kind="agent", author_id="agent-1",
    )
    assert updated["x"] == 9
