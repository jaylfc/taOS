import pytest
import pytest_asyncio
from pathlib import Path

from tinyagentos.projects.events import ProjectEventBroker
from tinyagentos.projects.project_store import ProjectStore
from tinyagentos.projects.canvas.store import ProjectCanvasStore
from tinyagentos.projects.canvas.snapshotter import CanvasSnapshotter
from tinyagentos.projects.canvas import mcp_tools as ct
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
    await ps.add_member(p["id"], "agent-1", member_kind="native")
    ctx = ct.CanvasToolContext(
        project_store=ps, canvas_store=cs, snapshotter=snap, data_root=data_root,
    )

    async def _grant_edit():
        await ps._db.execute(
            "UPDATE project_members SET can_edit_canvas = 1 "
            "WHERE project_id = ? AND member_id = ?",
            (p["id"], "agent-1"),
        )
        await ps._db.commit()

    yield p, ctx, ps, _grant_edit
    await snap.stop()
    await cs.close()
    await ps.close()


@pytest.mark.asyncio
async def test_canvas_add_note_creates_element(env):
    p, ctx, _ps, grant = env
    await grant()
    res = await ct.canvas_add_note(
        ctx, project_id=p["id"], agent_id="agent-1",
        text="agent-said-hello", x=10, y=20,
    )
    assert res["element"]["kind"] == "note"
    assert res["element"]["author_kind"] == "agent"
    assert res["element"]["author_id"] == "agent-1"


@pytest.mark.asyncio
async def test_canvas_add_text_creates_element(env):
    p, ctx, _ps, grant = env
    await grant()
    res = await ct.canvas_add_text(
        ctx, project_id=p["id"], agent_id="agent-1",
        text="an idea", x=5, y=5, font_size=20,
    )
    el = res["element"]
    assert el["kind"] == "text"
    assert el["author_kind"] == "agent"
    assert el["payload"]["text"] == "an idea"
    assert el["payload"]["font_size"] == 20


@pytest.mark.asyncio
async def test_canvas_add_mermaid_and_flowchart_carry_source(env):
    p, ctx, _ps, grant = env
    await grant()
    m = await ct.canvas_add_mermaid(
        ctx, project_id=p["id"], agent_id="agent-1",
        source="graph TD; A-->B", x=0, y=0,
    )
    assert m["element"]["kind"] == "mermaid"
    assert m["element"]["payload"]["source"] == "graph TD; A-->B"
    f = await ct.canvas_add_flowchart(
        ctx, project_id=p["id"], agent_id="agent-1",
        source="flowchart LR; a-->b", x=0, y=0,
    )
    assert f["element"]["kind"] == "flowchart"
    assert f["element"]["payload"]["source"] == "flowchart LR; a-->b"


@pytest.mark.asyncio
async def test_canvas_add_mindmap_edge_links_endpoints(env):
    p, ctx, _ps, grant = env
    await grant()
    a = await ct.canvas_add_text(
        ctx, project_id=p["id"], agent_id="agent-1", text="a", x=0, y=0,
    )
    b = await ct.canvas_add_text(
        ctx, project_id=p["id"], agent_id="agent-1", text="b", x=300, y=0,
    )
    edge = await ct.canvas_add_mindmap_edge(
        ctx, project_id=p["id"], agent_id="agent-1",
        from_id=a["element"]["id"], to_id=b["element"]["id"],
    )
    el = edge["element"]
    assert el["kind"] == "mindmap_edge"
    assert el["payload"]["from"] == a["element"]["id"]
    assert el["payload"]["to"] == b["element"]["id"]
    # The edge bbox spans its endpoints (centers), not a 1x1 box at the origin.
    assert el["w"] > 1


@pytest.mark.asyncio
async def test_canvas_add_mindmap_edge_rejects_unknown_endpoint(env):
    p, ctx, _ps, grant = env
    await grant()
    a = await ct.canvas_add_text(
        ctx, project_id=p["id"], agent_id="agent-1", text="a", x=0, y=0,
    )
    res = await ct.canvas_add_mindmap_edge(
        ctx, project_id=p["id"], agent_id="agent-1",
        from_id=a["element"]["id"], to_id="cve-does-not-exist",
    )
    assert res.get("error") == "not_found"


@pytest.mark.asyncio
async def test_canvas_add_denied_without_permission(env):
    p, ctx, _ps, _grant = env
    res = await ct.canvas_add_note(
        ctx, project_id=p["id"], agent_id="agent-1",
        text="x", x=0, y=0,
    )
    assert res["error"] == "permission_denied"


@pytest.mark.asyncio
async def test_canvas_list_denied_without_read(env):
    p, ctx, _ps, grant = env
    # can_edit_canvas only: the read checkbox stays off, so the in-process
    # read path must be blocked even though the agent can write.
    await grant()
    res = await ct.canvas_list_elements(
        ctx, project_id=p["id"], agent_id="agent-1"
    )
    assert res["error"] == "permission_denied"


@pytest.mark.asyncio
async def test_canvas_list_allowed_with_read(env):
    p, ctx, ps, grant = env
    await grant()
    await ps._db.execute(
        "UPDATE project_members SET can_read_canvas = 1 "
        "WHERE project_id = ? AND member_id = ?",
        (p["id"], "agent-1"),
    )
    await ps._db.commit()
    el = await ctx.canvas_store.add_element(
        project_id=p["id"], author_kind="user", author_id="u",
        element={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {"text": "x"}},
    )
    res = await ct.canvas_list_elements(
        ctx, project_id=p["id"], agent_id="agent-1"
    )
    assert "elements" in res
    assert [e["id"] for e in res["elements"]] == [el["id"]]


@pytest.mark.asyncio
async def test_canvas_update_denied_without_permission(env):
    p, ctx, _ps, _grant = env
    # An agent without the edit flag cannot mutate an existing element.
    el = await ctx.canvas_store.add_element(
        project_id=p["id"], author_kind="user", author_id="u",
        element={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {"text": "x"}},
    )
    res = await ct.canvas_update_element(
        ctx, project_id=p["id"], agent_id="agent-1",
        element_id=el["id"], patch={"x": 99},
    )
    assert res["error"] == "permission_denied"


@pytest.mark.asyncio
async def test_canvas_update_succeeds_with_permission(env):
    p, ctx, ps, grant = env
    await grant()
    note = await ct.canvas_add_note(
        ctx, project_id=p["id"], agent_id="agent-1",
        text="x", x=0, y=0,
    )
    res = await ct.canvas_update_element(
        ctx, project_id=p["id"], agent_id="agent-1",
        element_id=note["element"]["id"], patch={"x": 99},
    )
    assert res["element"]["x"] == 99


@pytest.mark.asyncio
async def test_canvas_get_snapshot_png_writes_file(env):
    p, ctx, _ps, grant = env
    await grant()
    await ct.canvas_add_note(
        ctx, project_id=p["id"], agent_id="agent-1", text="x", x=0, y=0,
    )
    res = await ct.canvas_get_snapshot_png(ctx, project_id=p["id"])
    assert "file_path" in res
    assert Path(res["file_path"]).exists()
