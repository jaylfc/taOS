"""RED tests for canvas drawing recovery endpoints.

These tests exercise the two new GET routes and the new MCP tools that
expose an element's original tldraw payload and a project's legacy-element
listing. They are expected to FAIL before the implementation lands.
"""
from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token


def _bare(app):
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


async def _new_project(client, slug):
    resp = await client.post("/api/projects", json={"name": slug, "slug": slug})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _add_member(client, pid, member_id):
    await client._transport.app.state.project_store.add_member(pid, member_id, member_kind="native")


async def _mint_agent(client, project_id, scopes):
    app = client._transport.app
    registry = app.state.agent_registry
    grants = app.state.agent_grants
    priv, _pub = app.state.agent_registry_keypair
    rec = await registry.register(
        framework="grok",
        display_name="Grok",
        origin="external-selfjoin",
        handle="@grok",
    )
    cid = rec["canonical_id"]
    await registry.set_status(cid, "active")
    for scope in scopes:
        await grants.add_grant(cid, scope, project_id=project_id)
    token = mint_registry_token(
        cid, priv, user_id="u", framework="grok", project_id=project_id
    )
    return cid, token


async def _grant_canvas(client, pid, agent_id, *, read=None, edit=None):
    body = {}
    if read is not None:
        body["can_read_canvas"] = read
    if edit is not None:
        body["can_edit_canvas"] = edit
    resp = await client.patch(
        f"/api/projects/{pid}/canvas/permissions/{agent_id}", json=body
    )
    assert resp.status_code == 200, resp.text
    return resp


@pytest.fixture(autouse=True)
def _ensure_canvas_store(client):
    store = client._transport.app.state.project_canvas_store
    if store._db is not None:
        try:
            asyncio.get_event_loop().run_until_complete(store.close())
        except Exception:
            pass
    ps = client._transport.app.state.project_store
    store.db_path = ps.db_path
    asyncio.get_event_loop().run_until_complete(store.init())
    yield
    try:
        asyncio.get_event_loop().run_until_complete(store.close())
    except Exception:
        pass


@pytest_asyncio.fixture
async def _agent_stores(client):
    app = client._transport.app
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()
    yield
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


@pytest.mark.asyncio
class TestOriginalElementRoute:
    async def test_original_returns_verbatim_tldraw_shape_for_deleted_row(self, client):
        pid = await _new_project(client, "recovery-orig")
        shape_payload = {
            "tldraw_shape": {"type": "draw", "props": {"color": "red", "segments": [[0, 0], [10, 10]]}},
        }
        resp = await client.post(
            f"/api/projects/{pid}/canvas/elements",
            json={"kind": "user_shape", "x": 0, "y": 0, "w": 50, "h": 50, "payload": shape_payload},
        )
        assert resp.status_code == 201, resp.text
        el = resp.json()["element"]
        eid = el["id"]
        await client.delete(f"/api/projects/{pid}/canvas/elements/{eid}")
        resp = await client.get(f"/api/projects/{pid}/canvas/elements/{eid}/original")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["element_id"] == eid
        assert data["tldraw_shape"] == shape_payload["tldraw_shape"]
        assert data["payload"] == shape_payload
        assert data["deleted_at"] is not None

    async def test_original_other_project_404(self, client):
        pid_a = await _new_project(client, "recovery-orig-a")
        pid_b = await _new_project(client, "recovery-orig-b")
        shape_payload = {"tldraw_shape": {"type": "draw", "props": {}}}
        resp = await client.post(
            f"/api/projects/{pid_a}/canvas/elements",
            json={"kind": "user_shape", "x": 0, "y": 0, "w": 10, "h": 10, "payload": shape_payload},
        )
        assert resp.status_code == 201, resp.text
        eid = resp.json()["element"]["id"]
        resp = await client.get(f"/api/projects/{pid_b}/canvas/elements/{eid}/original")
        assert resp.status_code == 404

    async def test_original_unknown_id_404(self, client):
        pid = await _new_project(client, "recovery-orig-unknown")
        resp = await client.get(f"/api/projects/{pid}/canvas/elements/nonexistent/original")
        assert resp.status_code == 404

    async def test_agent_token_with_canvas_read_can_fetch_original(self, client, _agent_stores):
        pid = await _new_project(client, "recovery-orig-agent")
        cid, token = await _mint_agent(client, pid, ("canvas_read",))
        await _add_member(client, pid, cid)
        await _grant_canvas(client, pid, cid, read=True)
        shape_payload = {"tldraw_shape": {"type": "draw", "props": {}}}
        resp = await client.post(
            f"/api/projects/{pid}/canvas/elements",
            json={"kind": "user_shape", "x": 0, "y": 0, "w": 10, "h": 10, "payload": shape_payload},
        )
        assert resp.status_code == 201, resp.text
        eid = resp.json()["element"]["id"]
        await client.delete(f"/api/projects/{pid}/canvas/elements/{eid}")
        async with _bare(client._transport.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/elements/{eid}/original",
                headers=_hdr(token),
            )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["tldraw_shape"] == shape_payload["tldraw_shape"]

    async def test_agent_token_without_canvas_read_is_403(self, client, _agent_stores):
        pid = await _new_project(client, "recovery-orig-403")
        cid, token = await _mint_agent(client, pid, ("canvas_write",))
        await _add_member(client, pid, cid)
        await _grant_canvas(client, pid, cid, read=True)
        shape_payload = {"tldraw_shape": {"type": "draw", "props": {}}}
        resp = await client.post(
            f"/api/projects/{pid}/canvas/elements",
            json={"kind": "user_shape", "x": 0, "y": 0, "w": 10, "h": 10, "payload": shape_payload},
        )
        assert resp.status_code == 201, resp.text
        eid = resp.json()["element"]["id"]
        async with _bare(client._transport.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/elements/{eid}/original",
                headers=_hdr(token),
            )
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestLegacyElementsRoute:
    async def test_legacy_lists_deleted_rows_when_asked(self, client):
        pid = await _new_project(client, "recovery-legacy")
        shape_payload = {"tldraw_shape": {"type": "draw", "props": {"segments": []}}}
        resp = await client.post(
            f"/api/projects/{pid}/canvas/elements",
            json={"kind": "user_shape", "x": 1, "y": 2, "w": 30, "h": 40, "payload": shape_payload},
        )
        assert resp.status_code == 201, resp.text
        eid = resp.json()["element"]["id"]
        await client.delete(f"/api/projects/{pid}/canvas/elements/{eid}")
        resp = await client.get(f"/api/projects/{pid}/canvas/legacy?include_deleted=1")
        assert resp.status_code == 200, resp.text
        rows = resp.json()["elements"]
        ids = [r["element_id"] for r in rows]
        assert eid in ids
        row = next(r for r in rows if r["element_id"] == eid)
        assert row["type"] == "draw"
        assert row["x"] == 1
        assert row["y"] == 2
        assert row["w"] == 30
        assert row["h"] == 40

    async def test_legacy_excludes_deleted_by_default(self, client):
        pid = await _new_project(client, "recovery-legacy-nodelete")
        shape_payload = {"tldraw_shape": {"type": "draw", "props": {}}}
        resp = await client.post(
            f"/api/projects/{pid}/canvas/elements",
            json={"kind": "user_shape", "x": 0, "y": 0, "w": 10, "h": 10, "payload": shape_payload},
        )
        assert resp.status_code == 201, resp.text
        eid = resp.json()["element"]["id"]
        await client.delete(f"/api/projects/{pid}/canvas/elements/{eid}")
        resp = await client.get(f"/api/projects/{pid}/canvas/legacy")
        assert resp.status_code == 200, resp.text
        ids = [r["element_id"] for r in resp.json()["elements"]]
        assert eid not in ids

    async def test_agent_token_with_canvas_read_can_list_legacy(self, client, _agent_stores):
        pid = await _new_project(client, "recovery-legacy-agent")
        cid, token = await _mint_agent(client, pid, ("canvas_read",))
        await _add_member(client, pid, cid)
        await _grant_canvas(client, pid, cid, read=True)
        shape_payload = {"tldraw_shape": {"type": "draw", "props": {}}}
        resp = await client.post(
            f"/api/projects/{pid}/canvas/elements",
            json={"kind": "user_shape", "x": 0, "y": 0, "w": 10, "h": 10, "payload": shape_payload},
        )
        assert resp.status_code == 201, resp.text
        eid = resp.json()["element"]["id"]
        await client.delete(f"/api/projects/{pid}/canvas/elements/{eid}")
        async with _bare(client._transport.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/legacy?include_deleted=1",
                headers=_hdr(token),
            )
        assert resp.status_code == 200, resp.text
        ids = [r["element_id"] for r in resp.json()["elements"]]
        assert eid in ids

    async def test_agent_token_without_canvas_read_is_403(self, client, _agent_stores):
        pid = await _new_project(client, "recovery-legacy-403")
        cid, token = await _mint_agent(client, pid, ("canvas_write",))
        await _add_member(client, pid, cid)
        await _grant_canvas(client, pid, cid, read=True)
        async with _bare(client._transport.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/legacy?include_deleted=1",
                headers=_hdr(token),
            )
        assert resp.status_code == 403
