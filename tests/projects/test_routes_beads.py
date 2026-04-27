"""Integration tests for the Beads bridge wired into the FastAPI app.

Uses the existing 'client' fixture which builds a real app via create_app
with a tmp data dir. Bridge is exercised end-to-end: route hooks, chat
hooks, broker subscription, JSONL writes.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_app_boots_with_beads_bridge(app):
    """The bridge must be attached to app.state on lifespan startup."""
    async with app.router.lifespan_context(app):
        bridge = app.state.beads_bridge
        assert bridge is not None
        # data_root should be data_dir/projects
        assert bridge._data_root.name == "projects"


def _auth_client(app):
    """Return a session-cookie-authenticated AsyncClient for the given app."""
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
    )


@pytest.mark.asyncio
async def test_create_task_marks_project_dirty(app):
    async with app.router.lifespan_context(app):
        async with _auth_client(app) as c:
            r = await c.post(
                "/api/projects",
                json={"name": "Demo", "slug": "demo-marks-dirty"},
            )
            assert r.status_code == 200, r.text
            project = r.json()

            bridge = app.state.beads_bridge
            bridge._dirty.clear()

            r = await c.post(
                f"/api/projects/{project['id']}/tasks",
                json={"title": "T1"},
            )
            assert r.status_code == 200, r.text

            assert project["id"] in bridge._dirty


@pytest.mark.asyncio
async def test_export_endpoint_writes_jsonl(app):
    async with app.router.lifespan_context(app):
        async with _auth_client(app) as c:
            r = await c.post(
                "/api/projects",
                json={"name": "Demo", "slug": "demo-export"},
            )
            project = r.json()
            await c.post(
                f"/api/projects/{project['id']}/tasks",
                json={"title": "Hello world"},
            )
            r = await c.post(f"/api/projects/{project['id']}/beads/export")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["path"].endswith("/.beads/tasks.jsonl")
            p = Path(body["path"])
            assert p.exists()
            line = p.read_text().strip()
            assert json.loads(line)["title"] == "Hello world"
