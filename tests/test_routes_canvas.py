"""Endpoint tests for tinyagentos/routes/canvas.py."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from taos_test_csrf import arm_test_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client(tmp_path):
    from tinyagentos.app import create_app

    config = {
        "server": {"host": "0.0.0.0", "port": 6969},
        "backends": [],
        "qmd": {"url": "http://localhost:7832"},
        "agents": [],
        "metrics": {"poll_interval": 30, "retention_days": 30},
    }
    import yaml

    (tmp_path / "config.yaml").write_text(yaml.dump(config))
    (tmp_path / ".setup_complete").touch()
    app = create_app(data_dir=tmp_path)
    app.state.auth.setup_user("admin", "Admin", "", "adminpass")
    return app


@pytest.fixture()
def ws_app(tmp_path):
    return _make_client(tmp_path)


@pytest.fixture()
def unauthed_client(ws_app):
    with TestClient(ws_app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture()
def authed_client(ws_app):
    record = ws_app.state.auth.find_user("admin")
    token = ws_app.state.auth.create_session(user_id=record["id"], long_lived=True)
    with TestClient(ws_app, raise_server_exceptions=False) as c:
        c.cookies.set("taos_session", token)
        arm_test_client(c)
        yield c


# ---------------------------------------------------------------------------
# POST /api/canvas/generate
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_canvas_returns_200(client):
    resp = await client.post("/api/canvas/generate", json={"title": "Test"})
    assert resp.status_code == 200
    data = resp.json()
    assert "canvas_id" in data
    assert "canvas_url" in data
    assert "edit_token" in data
    assert data["canvas_url"] == f"/canvas/{data['canvas_id']}"


@pytest.mark.asyncio
async def test_create_canvas_stores_values(client):
    resp = await client.post(
        "/api/canvas/generate",
        json={"title": "My Canvas", "content": "hello", "style": "dark", "format": "html"},
    )
    assert resp.status_code == 200
    data = resp.json()
    canvas_id = data["canvas_id"]
    edit_token = data["edit_token"]

    fetched = await client.get(f"/api/canvas/{canvas_id}/data")
    assert fetched.status_code == 200
    stored = fetched.json()
    assert stored["title"] == "My Canvas"
    assert stored["content"] == "hello"
    assert stored["style"] == "dark"
    assert stored["format"] == "html"
    assert stored["edit_token"] == edit_token


# ---------------------------------------------------------------------------
# GET /api/canvas/{canvas_id}/data
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_canvas_data_404_for_missing(client):
    resp = await client.get("/api/canvas/does-not-exist/data")
    assert resp.status_code == 404
    assert "error" in resp.json()


@pytest.mark.asyncio
async def test_get_canvas_data_returns_fields(client):
    created = await client.post("/api/canvas/generate", json={"title": "T"})
    canvas_id = created.json()["canvas_id"]
    resp = await client.get(f"/api/canvas/{canvas_id}/data")
    assert resp.status_code == 200
    data = resp.json()
    for key in ("id", "title", "content", "style", "format", "created_by", "edit_token"):
        assert key in data, f"missing key: {key}"


# ---------------------------------------------------------------------------
# POST /api/canvas/{canvas_id}/update
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_canvas_happy_path(client):
    created = await client.post("/api/canvas/generate", json={"title": "Old"})
    canvas_id = created.json()["canvas_id"]
    edit_token = created.json()["edit_token"]

    resp = await client.post(
        f"/api/canvas/{canvas_id}/update",
        json={"edit_token": edit_token, "content": "new content", "title": "New"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "updated"

    fetched = await client.get(f"/api/canvas/{canvas_id}/data")
    assert fetched.json()["content"] == "new content"
    assert fetched.json()["title"] == "New"


@pytest.mark.asyncio
async def test_update_canvas_wrong_token_returns_403(client):
    created = await client.post("/api/canvas/generate", json={"title": "T"})
    canvas_id = created.json()["canvas_id"]

    resp = await client.post(
        f"/api/canvas/{canvas_id}/update",
        json={"edit_token": "wrong-token", "content": "x"},
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_update_canvas_missing_canvas_returns_403(client):
    resp = await client.post(
        "/api/canvas/does-not-exist/update",
        json={"edit_token": "some-token", "content": "x"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/canvas/{canvas_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_canvas_happy_path(client):
    created = await client.post("/api/canvas/generate", json={"title": "ToDelete"})
    canvas_id = created.json()["canvas_id"]

    resp = await client.delete(f"/api/canvas/{canvas_id}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    follow_up = await client.get(f"/api/canvas/{canvas_id}/data")
    assert follow_up.status_code == 404


@pytest.mark.asyncio
async def test_delete_canvas_missing_returns_404(client):
    resp = await client.delete("/api/canvas/does-not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/canvas
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_canvases_empty_by_default(client):
    resp = await client.get("/api/canvas")
    assert resp.status_code == 200
    data = resp.json()
    assert "canvases" in data
    assert data["canvases"] == []


@pytest.mark.asyncio
async def test_list_canvases_returns_created(client):
    created = await client.post("/api/canvas/generate", json={"title": "A"})
    cid = created.json()["canvas_id"]

    resp = await client.get("/api/canvas")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["canvases"]) == 1
    assert data["canvases"][0]["id"] == cid


# ---------------------------------------------------------------------------
# WS /ws/canvas/{canvas_id}
# ---------------------------------------------------------------------------


def test_ws_canvas_unauthenticated_rejected(unauthed_client):
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with unauthed_client.websocket_connect("/ws/canvas/test-canvas-id") as ws:
            ws.receive_text()
    assert exc_info.value.code == 1008


def test_ws_canvas_authenticated_accepted(authed_client):
    with authed_client.websocket_connect("/ws/canvas/test-canvas-id") as ws:
        ws.send_text("ping")
        # Connection staying open means auth passed and hub.join succeeded.
