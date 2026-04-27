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


@pytest.mark.asyncio
async def test_a2a_claim_verb_via_rest_post_claims_task(app):
    """Send '/claim tsk_<id>' as a non-system message into the project's
    A2A channel via REST. Bridge should claim the task without invoking
    agent dispatch."""
    async with app.router.lifespan_context(app):
        async with _auth_client(app) as c:
            r = await c.post(
                "/api/projects",
                json={"name": "Demo", "slug": "demo-verb"},
            )
            project = r.json()

            # Create a task to claim
            r = await c.post(
                f"/api/projects/{project['id']}/tasks",
                json={"title": "claimable"},
            )
            task = r.json()

            # Find the project's A2A channel
            r = await c.get(f"/api/chat/channels?project_id={project['id']}")
            channels = r.json()["channels"]
            a2a = next(c2 for c2 in channels if (c2.get("settings") or {}).get("kind") == "a2a")

            # Send the verb as an "agent" non-system message
            r = await c.post(
                "/api/chat/messages",
                json={
                    "channel_id": a2a["id"],
                    "author_id": "alice",
                    "author_type": "agent",
                    "content": f"/claim {task['id']}",
                    "content_type": "text",
                },
            )
            assert r.status_code == 200, r.text

            # Allow a tick for the bridge to process the hook
            await asyncio.sleep(0.1)

            # Task should now be claimed by alice
            r = await c.get(
                f"/api/projects/{project['id']}/tasks/{task['id']}"
            )
            assert r.json()["status"] == "claimed"
            assert r.json()["claimed_by"] == "alice"


@pytest.mark.asyncio
async def test_a2a_mention_attaches_comment(app):
    async with app.router.lifespan_context(app):
        async with _auth_client(app) as c:
            r = await c.post(
                "/api/projects",
                json={"name": "Demo", "slug": "demo-mention"},
            )
            project = r.json()
            r = await c.post(
                f"/api/projects/{project['id']}/tasks",
                json={"title": "mentioned"},
            )
            task = r.json()
            r = await c.get(f"/api/chat/channels?project_id={project['id']}")
            channels = r.json()["channels"]
            a2a = next(c2 for c2 in channels if (c2.get("settings") or {}).get("kind") == "a2a")

            await c.post(
                "/api/chat/messages",
                json={
                    "channel_id": a2a["id"],
                    "author_id": "bob",
                    "author_type": "agent",
                    "content": f"chasing {task['id']} in prod",
                    "content_type": "text",
                },
            )
            await asyncio.sleep(0.1)

            r = await c.get(
                f"/api/projects/{project['id']}/tasks/{task['id']}/comments"
            )
            items = r.json()["items"]
            assert any(c2["author_id"] == "bob" and "prod" in c2["body"] for c2 in items)
