"""Fleet quarantine durability tests (tsk-gqbt2o).

RED-first:
  * test_bounce_records_a_strike  -- fails today because bounce_card() never
    records a strike through the board store; count stays 0.
  * test_burned_uses_the_durable_count -- fails today because _burned() only
    consults /tmp/taos-attempts-*; with the file absent it returns False even
    when the durable strike_count is already 3.
"""
from __future__ import annotations

import os

import pytest

from taos_test_csrf import csrf_event_hooks
from tinyagentos.fleet.executor import bounce_card
from tinyagentos.fleet.next_card import _burned
from httpx import ASGITransport, AsyncClient


def _auth_client(app):
    app.state.auth.setup_user("admin", "Test Admin", "", "testpass")
    record = app.state.auth.find_user("admin")
    uid = record["id"] if record else ""
    token = app.state.auth.create_session(user_id=uid, long_lived=True)
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        cookies={"taos_session": token},
        event_hooks=csrf_event_hooks(),
    )


async def _make_project_and_task(c, slug: str):
    r = await c.post("/api/projects", json={"name": "Demo", "slug": slug})
    assert r.status_code == 200, r.text
    project_id = r.json()["id"]
    r = await c.post(f"/api/projects/{project_id}/tasks", json={"title": "T1"})
    assert r.status_code == 200, r.text
    return project_id, r.json()["id"]


@pytest.mark.asyncio
async def test_bounce_records_a_strike(app):
    """A bounce must record a strike readable back from the store."""
    async with app.router.lifespan_context(app):
        async with _auth_client(app) as c:
            project_id, task_id = await _make_project_and_task(c, "bounce-strike")

            await bounce_card(
                task_id,
                app.state.project_task_store,
                app.state.task_strikes,
                log_tail="lane failure",
                actor="worker-1",
            )

            count = await app.state.task_strikes.count_strikes(task_id)
            assert count >= 1, f"strike_count stayed {count} after bounce_card"


@pytest.mark.asyncio
async def test_burned_uses_the_durable_count(app):
    """_burned() must return True when strike_count >= 3 even if /tmp is empty."""
    async with app.router.lifespan_context(app):
        async with _auth_client(app) as c:
            project_id, task_id = await _make_project_and_task(c, "burned-durable")

            strikes = app.state.task_strikes
            for _ in range(strikes.STRIKE_THRESHOLD):
                await strikes.record_strike(task_id, "dispatch_failed", actor="worker-1")

            attempts_file = f"/tmp/taos-attempts-{task_id}"
            if os.path.exists(attempts_file):
                os.remove(attempts_file)

            result = await _burned(
                task_id,
                app.state.project_task_store,
                app.state.task_strikes,
            )
            assert result is True, (
                "_burned() returned False with strike_count=3 and no /tmp file"
            )
