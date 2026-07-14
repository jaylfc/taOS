"""Endpoint tests for tinyagentos/routes/project_canvas.py."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest


@pytest.fixture(autouse=True)
def _ensure_canvas_store(client, tmp_path_factory):
    """Initialize project_canvas_store if the lifespan didn't run (test client)."""
    store = client._transport.app.state.project_canvas_store
    if store._db is not None:
        try:
            asyncio.get_event_loop().run_until_complete(store.close())
        except Exception:
            pass
    # Use a fresh DB per test session via tmp_path. BaseStore reads self.db_path
    # (a Path) in init(); the previous override set a non-existent `_db_path`
    # string attr, so init() silently fell back to the production canvas DB.
    tmp_dir = tmp_path_factory.mktemp("canvas_test")
    store.db_path = tmp_dir / "test_projects.db"
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
    resp = await client.patch(
        "/api/projects/proj-1/canvas/permissions/agent-1",
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
