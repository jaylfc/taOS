"""Endpoint tests for tinyagentos/routes/project_canvas.py."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tinyagentos.agent_registry_store import mint_registry_token


@pytest.fixture(autouse=True)
def _ensure_canvas_store(client, tmp_path_factory):
    """Initialize project_canvas_store if the lifespan didn't run (test client).

    Point the canvas store at the same projects.db the project_store uses, so
    the per-project canvas permission live in the shared project_members table
    (the store-level edit check reads from there, exactly as in production).
    """
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


# ---------------------------------------------------------------------------
# List elements
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_elements_returns_200(client):
    resp = await client.get("/api/projects/proj-1/canvas/elements")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_list_elements_returns_elements_key(client):
    data = (await client.get("/api/projects/proj-1/canvas/elements")).json()
    assert "elements" in data
    assert isinstance(data["elements"], list)


# ---------------------------------------------------------------------------
# Create element
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_note_element_returns_201(client):
    body = {
        "kind": "note",
        "x": 10, "y": 20, "w": 200, "h": 100,
        "payload": {"text": "hello", "color": "yellow", "font_size": 14},
    }
    resp = await client.post("/api/projects/proj-1/canvas/elements", json=body)
    assert resp.status_code == 201, resp.text


@pytest.mark.asyncio
async def test_create_note_element_response_shape(client):
    body = {
        "kind": "note",
        "x": 10, "y": 20, "w": 200, "h": 100,
        "payload": {"text": "hello", "color": "yellow", "font_size": 14},
    }
    data = (await client.post("/api/projects/proj-1/canvas/elements", json=body)).json()
    assert "element" in data
    el = data["element"]
    assert el["kind"] == "note"
    assert el["x"] == 10
    assert el["y"] == 20
    assert el["w"] == 200
    assert el["h"] == 100
    assert el["payload"]["text"] == "hello"
    assert "id" in el
    assert "project_id" in el


@pytest.mark.asyncio
async def test_create_element_invalid_kind_returns_422(client):
    """Pydantic Literal validation rejects invalid 'kind' with 422."""
    body = {"kind": "invalid", "x": 0, "y": 0, "w": 1, "h": 1}
    resp = await client.post("/api/projects/proj-1/canvas/elements", json=body)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_create_link_element_without_url_returns_400(client):
    body = {"kind": "link", "x": 0, "y": 0, "w": 100, "h": 50, "payload": {}}
    resp = await client.post("/api/projects/proj-1/canvas/elements", json=body)
    assert resp.status_code == 400
    assert resp.json()["error"] == "link element requires payload.url"


@pytest.mark.asyncio
async def test_create_link_element_with_url_fetches_metadata(client):
    body = {
        "kind": "link",
        "x": 0, "y": 0, "w": 100, "h": 50,
        "payload": {"url": "https://example.com"},
    }
    fake_meta = {
        "url": "https://example.com",
        "title": "Example",
        "description": "",
        "preview_image_url": "",
        "favicon_url": "",
        "fetched_at": 0.0,
    }
    with patch(
        "tinyagentos.routes.project_canvas.fetch_link_metadata",
        new_callable=AsyncMock,
        return_value=fake_meta,
    ):
        resp = await client.post("/api/projects/proj-1/canvas/elements", json=body)
    assert resp.status_code == 201, resp.text
    el = resp.json()["element"]
    assert el["kind"] == "link"
    assert el["payload"]["title"] == "Example"


@pytest.mark.asyncio
async def test_create_image_element_returns_201(client):
    body = {
        "kind": "image",
        "x": 5, "y": 5, "w": 300, "h": 200,
        "payload": {"alt": "a photo"},
    }
    resp = await client.post("/api/projects/proj-1/canvas/elements", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["element"]["kind"] == "image"


@pytest.mark.asyncio
async def test_create_user_shape_element_returns_201(client):
    body = {
        "kind": "user_shape",
        "x": 0, "y": 0, "w": 50, "h": 50,
        "payload": {"shape": "rectangle"},
    }
    resp = await client.post("/api/projects/proj-1/canvas/elements", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["element"]["kind"] == "user_shape"


# ---------------------------------------------------------------------------
# Slice 4: element scoping (element_id tag + list filter).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_note_with_element_id_returns_201(client):
    body = {
        "kind": "note",
        "x": 0, "y": 0, "w": 100, "h": 50,
        "payload": {"text": "tagged"},
        "element_id": "elm-1",
    }
    resp = await client.post("/api/projects/proj-1/canvas/elements", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["element"]["element_id"] == "elm-1"


@pytest.mark.asyncio
async def test_create_note_without_element_id_is_untagged(client):
    body = {
        "kind": "note",
        "x": 0, "y": 0, "w": 100, "h": 50,
        "payload": {"text": "untagged"},
    }
    resp = await client.post("/api/projects/proj-1/canvas/elements", json=body)
    assert resp.status_code == 201, resp.text
    assert resp.json()["element"]["element_id"] is None


@pytest.mark.asyncio
async def test_list_elements_filters_by_element_id(client):
    tagged = (
        await client.post(
            "/api/projects/proj-1/canvas/elements",
            json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1,
                  "payload": {"text": "t"}, "element_id": "elm-1"},
        )
    ).json()["element"]
    await client.post(
        "/api/projects/proj-1/canvas/elements",
        json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {"text": "u"}},
    )
    data = (
        await client.get("/api/projects/proj-1/canvas/elements?element_id=elm-1")
    ).json()
    assert [e["id"] for e in data["elements"]] == [tagged["id"]]


@pytest.mark.asyncio
async def test_list_elements_none_sentinel_returns_untagged(client):
    await client.post(
        "/api/projects/proj-1/canvas/elements",
        json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1,
              "payload": {"text": "t"}, "element_id": "elm-1"},
    )
    untagged = (
        await client.post(
            "/api/projects/proj-1/canvas/elements",
            json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1, "payload": {"text": "u"}},
        )
    ).json()["element"]
    data = (
        await client.get("/api/projects/proj-1/canvas/elements?element_id=none")
    ).json()
    assert [e["id"] for e in data["elements"]] == [untagged["id"]]


# ---------------------------------------------------------------------------
# Ideas-board kinds (#68): text, mermaid, flowchart, mindmap_edge.
# Content travels in the free-form payload, so each just needs to round-trip.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("text", {"text": "an idea", "font_size": 18}),
        ("mermaid", {"source": "graph TD; A-->B"}),
        ("flowchart", {"source": "flowchart LR; start-->end"}),
        ("mindmap_edge", {"from": "cve-a", "to": "cve-b"}),
    ],
)
@pytest.mark.asyncio
async def test_create_ideas_board_kind_round_trips(client, kind, payload):
    body = {"kind": kind, "x": 5, "y": 5, "w": 120, "h": 60, "payload": payload}
    resp = await client.post("/api/projects/proj-1/canvas/elements", json=body)
    assert resp.status_code == 201, resp.text
    el = resp.json()["element"]
    assert el["kind"] == kind
    assert el["payload"] == payload


@pytest.mark.asyncio
async def test_ideas_board_kind_appears_in_list(client):
    body = {
        "kind": "mermaid",
        "x": 0, "y": 0, "w": 200, "h": 120,
        "payload": {"source": "graph TD; X-->Y"},
    }
    await client.post("/api/projects/proj-1/canvas/elements", json=body)
    data = (await client.get("/api/projects/proj-1/canvas/elements")).json()
    kinds = [e["kind"] for e in data["elements"]]
    assert "mermaid" in kinds


# ---------------------------------------------------------------------------
# Update element
# ---------------------------------------------------------------------------


async def _create_note(client, project_id="proj-1"):
    body = {
        "kind": "note",
        "x": 0, "y": 0, "w": 100, "h": 50,
        "payload": {"text": "original"},
    }
    resp = await client.post(f"/api/projects/{project_id}/canvas/elements", json=body)
    return resp.json()["element"]


@pytest.mark.asyncio
async def test_update_element_returns_200(client):
    el = await _create_note(client)
    resp = await client.patch(
        f"/api/projects/proj-1/canvas/elements/{el['id']}",
        json={"x": 99, "payload": {"text": "edited"}},
    )
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_update_element_applies_patch(client):
    el = await _create_note(client)
    data = (await client.patch(
        f"/api/projects/proj-1/canvas/elements/{el['id']}",
        json={"x": 99, "payload": {"text": "edited"}},
    )).json()
    updated = data["element"]
    assert updated["x"] == 99
    assert updated["payload"]["text"] == "edited"


@pytest.mark.asyncio
async def test_update_element_not_found_returns_404(client):
    resp = await client.patch(
        "/api/projects/proj-1/canvas/elements/nonexistent",
        json={"x": 1},
    )
    assert resp.status_code == 404
    assert "error" in resp.json()


# ---------------------------------------------------------------------------
# Delete element
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_element_returns_204(client):
    el = await _create_note(client)
    resp = await client.delete(f"/api/projects/proj-1/canvas/elements/{el['id']}")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_element_removes_from_list(client):
    el = await _create_note(client)
    await client.delete(f"/api/projects/proj-1/canvas/elements/{el['id']}")
    data = (await client.get("/api/projects/proj-1/canvas/elements")).json()
    ids = [e["id"] for e in data["elements"]]
    assert el["id"] not in ids


@pytest.mark.asyncio
async def test_delete_element_not_found_returns_204(client):
    """Delete of a nonexistent element returns 204 (soft-delete is idempotent)."""
    resp = await client.delete("/api/projects/proj-1/canvas/elements/nonexistent")
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# Snapshot PNG
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_png_project_not_found_returns_404(client):
    resp = await client.get("/api/projects/nonexistent/canvas/snapshot.png")
    assert resp.status_code == 404
    assert resp.json()["error"] == "project not found"


# ---------------------------------------------------------------------------
# Snapshot TLDR
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_tldr_requires_snapshotter(client):
    """snapshot.tldr needs a live CanvasSnapshotter (container backend); skip."""
    snap = client._transport.app.state.canvas_snapshotter
    if snap is None:
        pytest.skip("canvas_snapshotter not available; needs container backend")
    resp = await client.get("/api/projects/nonexistent/canvas/snapshot.tldr")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_permission_member_not_found_returns_404(client):
    resp = await client.post("/api/projects", json={"name": "perm", "slug": "perm"})
    assert resp.status_code == 200, resp.text
    pid = resp.json()["id"]
    resp = await client.patch(
        f"/api/projects/{pid}/canvas/permissions/agent-1",
        json={"can_edit_canvas": True},
    )
    assert resp.status_code == 404
    assert resp.json()["error"] == "member not found"


# ---------------------------------------------------------------------------
# SSE stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_canvas_stream_endpoint_exists(client):
    """canvas.stream is an infinite SSE endpoint; verify it is registered."""
    # The stream endpoint is infinite, so a normal client.get() would block
    # forever. Instead, confirm the route is registered on the app.
    app = client._transport.app
    paths = [r.path for r in app.routes]
    assert "/api/projects/{project_id}/canvas/stream" in paths


# ---------------------------------------------------------------------------
# Finding 2 (adversarial review): session users must be gated by project
# visibility (owner/admin). A non-owner collapses into the same existence-hiding
# 404 the agent path uses (D3 matrix); owner/admin stays allowed, and an
# allowed session write is still attributed to the verified user.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSessionCanvasOwnerGating:
    async def test_owner_session_write_allowed(self, ctx):
        pid = await _new_project(ctx, "owner-gate-write")
        resp = await ctx.client.post(
            f"/api/projects/{pid}/canvas/elements",
            json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1,
                  "payload": {"text": "owned"}},
        )
        assert resp.status_code == 201, resp.text

    async def test_non_owner_session_list_is_404(self, ctx):
        pid = await _new_project(ctx, "nonowner-gate")
        async with _non_owner_client(ctx.app) as other:
            resp = await other.get(f"/api/projects/{pid}/canvas/elements")
        assert resp.status_code == 404
        assert resp.status_code != 403
        assert resp.status_code != 200

    async def test_non_owner_session_write_is_404(self, ctx):
        pid = await _new_project(ctx, "nonowner-gate-write")
        async with _non_owner_client(ctx.app) as other:
            resp = await other.post(
                f"/api/projects/{pid}/canvas/elements",
                json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1,
                      "payload": {"text": "x"}},
            )
        assert resp.status_code == 404
        assert resp.status_code != 403


# ---------------------------------------------------------------------------
# Slice 3: agent scope + per-project canvas permission gating (D3 matrix)
# plus honest attribution and the uniform actor stamp (D4).
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def ctx(client):
    """Reuse the session-admin `client` app and init the agent registry +
    grants stores so canvas agent tokens can be minted against real stores."""
    app = client._transport.app
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr)
        if store._db is None:
            await store.init()
    # The test client bypasses the lifespan, so app.state.project_event_broker
    # is unset; point it at the broker the canvas store already publishes to.
    if getattr(app.state, "project_event_broker", None) is None:
        app.state.project_event_broker = app.state.project_canvas_store._broker
    uid = app.state.auth.find_user("admin")["id"]
    yield SimpleNamespace(client=client, app=app, uid=uid)
    for attr in ("agent_registry", "agent_grants"):
        store = getattr(app.state, attr)
        if store._db is not None:
            await store.close()


def _bare(app):
    """Cookieless client so requests carry only the Bearer header."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _hdr(token):
    return {"Authorization": f"Bearer {token}"}


async def _new_project(ctx, slug):
    resp = await ctx.client.post("/api/projects", json={"name": slug, "slug": slug})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _non_owner_client(app):
    """A non-owner, non-admin session client for a second human user.

    The project under test is owned by the admin `client` fixture user, so a
    session minted for this user is an "other" human per the D3 matrix and must
    collapse into an existence-hiding 404 on the canvas.
    """
    auth = app.state.auth
    code = auth.add_user_invite("other", "admin")
    rec = auth.complete_invite(
        "other", code, "Other User", "o@example.com", "password123"
    )
    token = auth.create_session(user_id=rec["id"], long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )


async def _add_member(ctx, pid, member_id):
    await ctx.app.state.project_store.add_member(pid, member_id, member_kind="native")


async def _mint_agent(ctx, project_id, scopes):
    registry = ctx.app.state.agent_registry
    grants = ctx.app.state.agent_grants
    priv, _pub = ctx.app.state.agent_registry_keypair
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


async def _grant_canvas(ctx, pid, agent_id, *, read=None, edit=None):
    body = {}
    if read is not None:
        body["can_read_canvas"] = read
    if edit is not None:
        body["can_edit_canvas"] = edit
    resp = await ctx.client.patch(
        f"/api/projects/{pid}/canvas/permissions/{agent_id}", json=body
    )
    assert resp.status_code == 200, resp.text
    return resp


@pytest.mark.asyncio
class TestAgentCanvasReadGating:
    async def test_read_allowed_with_scope_and_flag(self, ctx):
        pid = await _new_project(ctx, "alpha")
        cid, token = await _mint_agent(ctx, pid, ("canvas_read",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, read=True)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/elements", headers=_hdr(token)
            )
        assert resp.status_code == 200

    async def test_read_without_scope_is_403(self, ctx):
        pid = await _new_project(ctx, "alpha")
        # canvas_write only: read scope is missing.
        cid, token = await _mint_agent(ctx, pid, ("canvas_write",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, read=True)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/elements", headers=_hdr(token)
            )
        assert resp.status_code == 403

    async def test_read_without_flag_is_403(self, ctx):
        pid = await _new_project(ctx, "alpha")
        cid, token = await _mint_agent(ctx, pid, ("canvas_read",))
        await _add_member(ctx, pid, cid)
        # No can_read_canvas grant.
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/elements", headers=_hdr(token)
            )
        assert resp.status_code == 403

@pytest.mark.asyncio
class TestAgentCanvasWriteGating:
    async def test_write_allowed_with_scope_and_flag(self, ctx):
        pid = await _new_project(ctx, "alpha")
        cid, token = await _mint_agent(ctx, pid, ("canvas_write",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, edit=True)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/canvas/elements",
                json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1,
                      "payload": {"text": "a"}},
                headers=_hdr(token),
            )
        assert resp.status_code == 201, resp.text

    async def test_write_without_scope_is_403(self, ctx):
        pid = await _new_project(ctx, "alpha")
        # canvas_read only: write scope is missing.
        cid, token = await _mint_agent(ctx, pid, ("canvas_read",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, edit=True)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/canvas/elements",
                json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1,
                      "payload": {"text": "a"}},
                headers=_hdr(token),
            )
        assert resp.status_code == 403

    async def test_write_without_flag_is_403(self, ctx):
        pid = await _new_project(ctx, "alpha")
        cid, token = await _mint_agent(ctx, pid, ("canvas_write",))
        await _add_member(ctx, pid, cid)
        # No can_edit_canvas grant.
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/canvas/elements",
                json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1,
                      "payload": {"text": "a"}},
                headers=_hdr(token),
            )
        assert resp.status_code == 403

    async def test_write_flag_does_not_grant_read(self, ctx):
        """The edit flag (and canvas_write scope) must NOT satisfy a read."""
        pid = await _new_project(ctx, "alpha")
        cid, token = await _mint_agent(ctx, pid, ("canvas_write",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, edit=True)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/elements", headers=_hdr(token)
            )
        assert resp.status_code == 403


@pytest.mark.asyncio
class TestAgentCanvasCrossProject:
    async def test_different_project_is_404(self, ctx):
        pid_a = await _new_project(ctx, "alpha")
        pid_b = await _new_project(ctx, "bravo")
        cid, token_a = await _mint_agent(ctx, pid_a, ("canvas_read",))
        await _add_member(ctx, pid_a, cid)
        await _grant_canvas(ctx, pid_a, cid, read=True)
        async with _bare(ctx.app) as bare:
            listing = await bare.get(
                f"/api/projects/{pid_b}/canvas/elements", headers=_hdr(token_a)
            )
        assert listing.status_code == 404


@pytest.mark.asyncio
class TestCanvasAttribution:
    async def test_agent_write_carry_agent_fields(self, ctx):
        pid = await _new_project(ctx, "alpha")
        cid, token = await _mint_agent(ctx, pid, ("canvas_write",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, edit=True)
        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/canvas/elements",
                json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1,
                      "payload": {"text": "a"}},
                headers=_hdr(token),
            )
        assert resp.status_code == 201, resp.text
        el = resp.json()["element"]
        assert el["author_kind"] == "agent"
        assert el["author_id"] == cid

    async def test_user_write_attributed_to_user_not_system(self, ctx):
        pid = await _new_project(ctx, "alpha")
        resp = await ctx.client.post(
            f"/api/projects/{pid}/canvas/elements",
            json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1,
                  "payload": {"text": "a"}},
        )
        assert resp.status_code == 201, resp.text
        el = resp.json()["element"]
        assert el["author_kind"] == "user"
        assert el["author_id"] == ctx.uid
        assert el["author_id"] != "system"


@pytest.mark.asyncio
class TestCanvasEventActor:
    """D4: a uniform actor object {kind, id} is stamped on every canvas.* event."""

    async def _capture(self, ctx, pid, coro):
        broker = ctx.app.state.project_event_broker
        queue = await broker.subscribe(pid)
        # Drain any replayed events from earlier actions in this test so we
        # observe only the event produced by the action under test.
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        await coro()
        return await asyncio.wait_for(queue.get(), timeout=5.0)

    async def test_actor_on_create_event(self, ctx):
        pid = await _new_project(ctx, "alpha")

        async def action():
            await ctx.client.post(
                f"/api/projects/{pid}/canvas/elements",
                json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1,
                      "payload": {"text": "a"}},
            )

        ev = await self._capture(ctx, pid, action)
        assert ev.kind == "canvas.element_added"
        assert ev.payload["actor"] == {"kind": "user", "id": ctx.uid}

    async def test_actor_on_delete_event(self, ctx):
        pid = await _new_project(ctx, "alpha")
        created = await ctx.client.post(
            f"/api/projects/{pid}/canvas/elements",
            json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1,
                  "payload": {"text": "a"}},
        )
        eid = created.json()["element"]["id"]

        async def action():
            await ctx.client.delete(
                f"/api/projects/{pid}/canvas/elements/{eid}"
            )

        ev = await self._capture(ctx, pid, action)
        assert ev.kind == "canvas.element_deleted"
        assert ev.payload["actor"] == {"kind": "user", "id": ctx.uid}

    async def test_actor_on_permission_change_event(self, ctx):
        pid = await _new_project(ctx, "alpha")
        cid, _token = await _mint_agent(ctx, pid, ("canvas_write",))
        await _add_member(ctx, pid, cid)

        async def action():
            await ctx.client.patch(
                f"/api/projects/{pid}/canvas/permissions/{cid}",
                json={"can_edit_canvas": True},
            )

        ev = await self._capture(ctx, pid, action)
        assert ev.kind == "canvas.permission_changed"
        assert ev.payload["actor"] == {"kind": "user", "id": ctx.uid}
        assert ev.payload["can_edit_canvas"] is True


@pytest.mark.asyncio
class TestPermissionPatchExtendsFlags:
    async def test_patch_can_set_read_flag_only(self, ctx):
        pid = await _new_project(ctx, "alpha")
        cid, _token = await _mint_agent(ctx, pid, ("canvas_read",))
        await _add_member(ctx, pid, cid)
        resp = await ctx.client.patch(
            f"/api/projects/{pid}/canvas/permissions/{cid}",
            json={"can_read_canvas": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["can_read_canvas"] is True
        assert resp.json()["can_edit_canvas"] is False
        members = await ctx.app.state.project_store.list_members(pid)
        me = next(m for m in members if m["member_id"] == cid)
        assert me["can_read_canvas"] == 1
        assert me["can_edit_canvas"] == 0

    async def test_patch_can_set_edit_flag_only(self, ctx):
        pid = await _new_project(ctx, "alpha")
        cid, _token = await _mint_agent(ctx, pid, ("canvas_write",))
        await _add_member(ctx, pid, cid)
        resp = await ctx.client.patch(
            f"/api/projects/{pid}/canvas/permissions/{cid}",
            json={"can_edit_canvas": True},
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["can_edit_canvas"] is True
        assert resp.json()["can_read_canvas"] is False

    async def test_patch_rejects_agent_token(self, ctx):
        """The permissions PATCH is owner/admin only; an agent token must not
        reach it (the middleware leaves it off the agent allowlist -> 401)."""
        pid = await _new_project(ctx, "alpha")
        cid, token = await _mint_agent(ctx, pid, ("canvas_write",))
        await _add_member(ctx, pid, cid)
        async with _bare(ctx.app) as bare:
            resp = await bare.patch(
                f"/api/projects/{pid}/canvas/permissions/{cid}",
                json={"can_edit_canvas": True},
                headers=_hdr(token),
            )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Slice 4: the SSE stream and the snapshot endpoints are gated by canvas_read
# for agent principals, and the keepalive tick re-checks the live read flag so
# a revoked agent cannot keep a long-lived stream open (lead-agent-identity
# design, Slice 4 + Edge cases).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCanvasStreamAgentGating:
    async def _connect_status(self, ctx, pid, token):
        """Open the SSE route directly so we never read the infinite body.

        A denied agent either raises HTTPException (missing scope) or returns a
        JSONResponse (flag off); an allowed agent returns a StreamingResponse.
        """
        from fastapi import HTTPException as _HTTPExc
        from starlette.responses import StreamingResponse as _SR
        from starlette.responses import JSONResponse as _JR
        from tinyagentos.routes.project_canvas import canvas_stream

        try:
            resp = await canvas_stream(pid, _stream_req(ctx.app, token=token))
        except _HTTPExc as exc:
            return exc.status_code
        if isinstance(resp, _SR):
            return 200
        if isinstance(resp, _JR):
            return resp.status_code
        return None

    async def test_stream_connect_allowed_with_read_scope_and_flag(self, ctx):
        pid = await _new_project(ctx, "stream-read")
        cid, token = await _mint_agent(ctx, pid, ("canvas_read",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, read=True)
        assert await self._connect_status(ctx, pid, token) == 200

    async def test_stream_connect_without_scope_is_403(self, ctx):
        pid = await _new_project(ctx, "stream-noscope")
        # canvas_write only: the read scope is missing.
        cid, token = await _mint_agent(ctx, pid, ("canvas_write",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, read=True)
        assert await self._connect_status(ctx, pid, token) == 403

    async def test_stream_connect_without_flag_is_403(self, ctx):
        pid = await _new_project(ctx, "stream-noflag")
        cid, token = await _mint_agent(ctx, pid, ("canvas_read",))
        await _add_member(ctx, pid, cid)
        # can_read_canvas flag is off.
        assert await self._connect_status(ctx, pid, token) == 403

    async def test_stream_closes_after_read_flag_cleared(self, ctx):
        """Slice 4 edge case: an agent principal's open stream must close once
        its can_read_canvas flag is cleared, bounded by the keepalive interval."""
        from tinyagentos.projects.events import ProjectEvent
        from tinyagentos.routes.project_canvas import canvas_stream

        pid = await _new_project(ctx, "stream-revoke")
        cid, token = await _mint_agent(ctx, pid, ("canvas_read",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, read=True)
        resp = await canvas_stream(pid, _stream_req(ctx.app, token=token))
        gen = resp.body_iterator
        broker = ctx.app.state.project_event_broker

        # A published event lets the generator emit a data frame immediately,
        # proving the agent principal's stream is live.
        async def _emit(n):
            await broker.publish(
                pid, ProjectEvent(kind="canvas.element_added", payload={"n": n})
            )

        await _emit(1)
        frame = await asyncio.wait_for(gen.__anext__(), timeout=5)
        assert frame.startswith("data: ")
        # Revoke read access while the stream is still open. The permissions
        # PATCH publishes a canvas.permission_changed event that the live stream
        # emits (proving liveness through events), then the next keepalive tick
        # re-checks the flag and closes the stream.
        await _grant_canvas(ctx, pid, cid, read=False)
        closed = False
        try:
            while True:
                await asyncio.wait_for(gen.__anext__(), timeout=12)
        except StopAsyncIteration:
            closed = True
        assert closed


@pytest.mark.asyncio
class TestCanvasSnapshotAgentGating:
    async def test_snapshot_png_allowed_with_read_scope_and_flag(self, ctx):
        pid = await _new_project(ctx, "snap-read")
        cid, token = await _mint_agent(ctx, pid, ("canvas_read",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, read=True)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/snapshot.png", headers=_hdr(token)
            )
        assert resp.status_code == 200
        assert resp.headers.get("content-type") == "image/png"

    async def test_snapshot_png_write_only_is_403(self, ctx):
        """Snapshots are READ scope only: a canvas_write-only token is denied."""
        pid = await _new_project(ctx, "snap-wo")
        cid, token = await _mint_agent(ctx, pid, ("canvas_write",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, read=True)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/snapshot.png", headers=_hdr(token)
            )
        assert resp.status_code == 403

    async def test_snapshot_png_without_flag_is_403(self, ctx):
        pid = await _new_project(ctx, "snap-noflag")
        cid, token = await _mint_agent(ctx, pid, ("canvas_read",))
        await _add_member(ctx, pid, cid)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/snapshot.png", headers=_hdr(token)
            )
        assert resp.status_code == 403

    async def test_snapshot_tldr_write_only_is_403(self, ctx):
        """Snapshots are READ scope only, even for the tldr endpoint."""
        pid = await _new_project(ctx, "snap-tldr-wo")
        cid, token = await _mint_agent(ctx, pid, ("canvas_write",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, read=True)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/snapshot.tldr", headers=_hdr(token)
            )
        assert resp.status_code == 403

    async def test_snapshot_tldr_allowed_with_read_scope_and_flag(self, ctx):
        pid = await _new_project(ctx, "snap-tldr-read")
        snap = ctx.app.state.canvas_snapshotter
        if snap is None:
            pytest.skip("canvas_snapshotter not available; needs container backend")
        cid, token = await _mint_agent(ctx, pid, ("canvas_read",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, read=True)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/snapshot.tldr", headers=_hdr(token)
            )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Slice 4 regression: session owner/admin behavior on stream + snapshots is
# unchanged by the agent gating.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCanvasStreamSnapshotSessionUnchanged:
    async def test_owner_stream_connect_allowed(self, ctx):
        from tinyagentos.routes.project_canvas import canvas_stream

        pid = await _new_project(ctx, "owner-stream")
        resp = await canvas_stream(
            pid, _stream_req(ctx.app, user_id=ctx.uid, is_admin=True)
        )
        from starlette.responses import StreamingResponse as _SR

        assert isinstance(resp, _SR)

    async def test_owner_snapshot_png_allowed(self, ctx):
        pid = await _new_project(ctx, "owner-snap")
        resp = await ctx.client.get(
            f"/api/projects/{pid}/canvas/snapshot.png"
        )
        assert resp.status_code == 200

    async def test_session_gating_uses_project_visibility(self, ctx):
        """A non-owner human session still collapses into 404 (D3 matrix),
        so agent gating did not change session semantics."""
        pid = await _new_project(ctx, "nonowner-snap")
        async with _non_owner_client(ctx.app) as other:
            resp = await other.get(
                f"/api/projects/{pid}/canvas/snapshot.png"
            )
        assert resp.status_code == 404

    async def test_owner_watch_projection_allowed(self, ctx):
        pid = await _new_project(ctx, "owner-watch")
        resp = await ctx.client.get(f"/api/projects/{pid}/canvas/watch-projection")
        assert resp.status_code == 200
        data = resp.json()
        assert "version" in data
        assert "elements" in data


@pytest.mark.asyncio
class TestWatchProjectionAgentGating:
    async def test_watch_projection_allowed_with_scope_and_flag(self, ctx):
        pid = await _new_project(ctx, "watch-read")
        cid, token = await _mint_agent(ctx, pid, ("canvas_read",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, read=True)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/watch-projection",
                headers=_hdr(token),
            )
        assert resp.status_code == 200

    async def test_watch_projection_without_scope_is_403(self, ctx):
        pid = await _new_project(ctx, "watch-noscope")
        cid, token = await _mint_agent(ctx, pid, ("canvas_write",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, read=True)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/watch-projection",
                headers=_hdr(token),
            )
        assert resp.status_code == 403

    async def test_watch_projection_without_flag_is_403(self, ctx):
        pid = await _new_project(ctx, "watch-noflag")
        cid, token = await _mint_agent(ctx, pid, ("canvas_read",))
        await _add_member(ctx, pid, cid)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid}/canvas/watch-projection",
                headers=_hdr(token),
            )
        assert resp.status_code == 403

    async def test_watch_projection_wrong_project_is_404(self, ctx):
        pid_a = await _new_project(ctx, "watch-alpha")
        pid_b = await _new_project(ctx, "watch-beta")
        cid, token_a = await _mint_agent(ctx, pid_a, ("canvas_read",))
        await _add_member(ctx, pid_a, cid)
        await _grant_canvas(ctx, pid_a, cid, read=True)
        async with _bare(ctx.app) as bare:
            resp = await bare.get(
                f"/api/projects/{pid_b}/canvas/watch-projection",
                headers=_hdr(token_a),
            )
        assert resp.status_code == 404



def _stream_req(app, *, token=None, user_id=None, is_admin=False):
    """Build a minimal Starlette Request wired to the real app.state so the
    canvas stream route can be invoked directly (no infinite-body client read)."""
    from starlette.requests import Request as _StarletteRequest

    headers = []
    if token is not None:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/x",
        "query_string": b"",
        "headers": headers,
        "app": app,
    }
    req = _StarletteRequest(scope)
    req.state.user_id = user_id
    req.state.is_admin = is_admin

    # is_disconnected() cancels its scope and awaits receive; an immediate,
    # non-awaiting receive returns a normal request message so the check
    # resolves to False (the stream is "connected").
    async def _receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    req._receive = _receive
    return req
# Slice 5: payload size cap (413) + agent write rate limit (429).
# ---------------------------------------------------------------------------


class TestCanvasPayloadSizeCap:
    """The 64 KB payload cap applies to all principals (agents and humans)."""

    @pytest.mark.asyncio
    async def test_create_oversized_payload_returns_413(self, ctx):
        pid = await _new_project(ctx, "payload-cap")
        oversized = "x" * (65 * 1024)
        body = {
            "kind": "text",
            "x": 0, "y": 0, "w": 100, "h": 50,
            "payload": {"text": oversized},
        }
        resp = await ctx.client.post(
            f"/api/projects/{pid}/canvas/elements", json=body,
        )
        assert resp.status_code == 413, resp.text

    @pytest.mark.asyncio
    async def test_patch_oversized_payload_returns_413(self, ctx):
        pid = await _new_project(ctx, "payload-cap-patch")
        el = await _create_note(ctx.client, pid)
        oversized = "x" * (65 * 1024)
        resp = await ctx.client.patch(
            f"/api/projects/{pid}/canvas/elements/{el['id']}",
            json={"payload": {"text": oversized}},
        )
        assert resp.status_code == 413, resp.text

    @pytest.mark.asyncio
    async def test_create_normal_payload_succeeds(self, ctx):
        pid = await _new_project(ctx, "payload-cap-ok")
        body = {
            "kind": "text",
            "x": 0, "y": 0, "w": 100, "h": 50,
            "payload": {"text": "normal content"},
        }
        resp = await ctx.client.post(
            f"/api/projects/{pid}/canvas/elements", json=body,
        )
        assert resp.status_code == 201, resp.text


class TestAgentWriteRateLimit:
    """Agents are throttled to 30 canvas writes per 60 s rolling window.
    Humans (session principals) are never throttled."""

    @pytest.mark.asyncio
    async def test_agent_exceeding_window_returns_429(self, ctx):
        import time
        import tinyagentos.routes.project_canvas as canvas_mod

        pid = await _new_project(ctx, "rate-limit-agent")
        cid, token = await _mint_agent(ctx, pid, ("canvas_write",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, edit=True)

        limiter = canvas_mod._canvas_write_limiter
        max_attempts = canvas_mod._CANVAS_WRITE_MAX_ATTEMPTS
        now = time.monotonic()
        with limiter._lock:
            limiter._log[cid] = [now - 1.0] * max_attempts

        async with _bare(ctx.app) as bare:
            resp = await bare.post(
                f"/api/projects/{pid}/canvas/elements",
                json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1,
                      "payload": {"text": "rate-limited"}},
                headers=_hdr(token),
            )
        assert resp.status_code == 429, resp.text

    @pytest.mark.asyncio
    async def test_agent_delete_exceeding_window_returns_429(self, ctx):
        import time
        import tinyagentos.routes.project_canvas as canvas_mod

        pid = await _new_project(ctx, "rate-limit-agent-del")
        cid, token = await _mint_agent(ctx, pid, ("canvas_write",))
        await _add_member(ctx, pid, cid)
        await _grant_canvas(ctx, pid, cid, edit=True)

        created = await ctx.client.post(
            f"/api/projects/{pid}/canvas/elements",
            json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1,
                  "payload": {"text": "to-delete"}},
        )
        assert created.status_code == 201
        eid = created.json()["element"]["id"]

        limiter = canvas_mod._canvas_write_limiter
        max_attempts = canvas_mod._CANVAS_WRITE_MAX_ATTEMPTS
        now = time.monotonic()
        with limiter._lock:
            limiter._log[cid] = [now - 1.0] * max_attempts

        async with _bare(ctx.app) as bare:
            resp = await bare.delete(
                f"/api/projects/{pid}/canvas/elements/{eid}",
                headers=_hdr(token),
            )
        assert resp.status_code == 429, resp.text

    @pytest.mark.asyncio
    async def test_human_write_not_rate_limited(self, ctx):
        pid = await _new_project(ctx, "rate-limit-human")
        for _ in range(31):
            resp = await ctx.client.post(
                f"/api/projects/{pid}/canvas/elements",
                json={"kind": "note", "x": 0, "y": 0, "w": 1, "h": 1,
                      "payload": {"text": "human-write"}},
            )
            assert resp.status_code == 201, resp.text


class TestCanvasGeometryClamping:
    @pytest.mark.asyncio
    async def test_extreme_coordinates_are_clamped_on_create(self, ctx):
        pid = await _new_project(ctx, "geom-clamp")
        resp = await ctx.client.post(
            f"/api/projects/{pid}/canvas/elements",
            json={
                "kind": "note",
                "x": 1_000_000_000,
                "y": -1_000_000_000,
                "w": 2_000_000_000,
                "h": -500,
            },
        )
        assert resp.status_code == 201, resp.text
        el = resp.json()["element"]
        assert el["x"] == 100_000
        assert el["y"] == -100_000
        assert el["w"] == 100_000
        assert el["h"] == 0

    @pytest.mark.asyncio
    async def test_extreme_coordinates_are_clamped_on_update(self, ctx):
        pid = await _new_project(ctx, "geom-clamp-patch")
        el = await _create_note(ctx.client, pid)
        resp = await ctx.client.patch(
            f"/api/projects/{pid}/canvas/elements/{el['id']}",
            json={"x": 1_000_000_000, "y": -1_000_000_000, "w": 2_000_000_000, "h": -500},
        )
        assert resp.status_code == 200, resp.text
        updated = resp.json()["element"]
        assert updated["x"] == 100_000
        assert updated["y"] == -100_000
        assert updated["w"] == 100_000
        assert updated["h"] == 0

    @pytest.mark.asyncio
    async def test_snapshot_render_dimensions_are_clamped(self, ctx):
        from pathlib import Path
        from PIL import Image

        pid = await _new_project(ctx, "snap-clamp")
        await ctx.client.post(
            f"/api/projects/{pid}/canvas/elements",
            json={
                "kind": "note",
                "x": 1_000_000,
                "y": 1_000_000,
                "w": 1_000_000,
                "h": 1_000_000,
                "payload": {"text": "extreme"},
            },
        )
        resp = await ctx.client.get(f"/api/projects/{pid}/canvas/snapshot.png")
        assert resp.status_code == 200, resp.text
        project = await ctx.app.state.project_store.get_project(pid)
        target = (
            Path(ctx.app.state.projects_root)
            / project["slug"]
            / "files"
            / "canvas"
            / "snapshot.png"
        )
        assert target.exists(), f"snapshot not rendered at {target}"
        with Image.open(target) as img:
            assert img.width <= 8192
            assert img.height <= 8192

