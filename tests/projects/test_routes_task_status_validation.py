"""RED tests for task status and relationship direction validation (tsk-funpc4).

Before the fix:
  * PATCH /api/projects/{pid}/tasks/{tid} with status="bogus" silently drops
    the write and returns 200, making the card vanish from every column.
  * GET /api/projects/{pid}/tasks?status=<anything> is accepted without
    validation, returning an empty list on a typo.
  * GET /api/projects/{pid}/tasks/{tid}/relationships?direction=<anything
    else> raises ValueError inside the store and answers 500.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def _project(client, slug: str) -> str:
    resp = await client.post("/api/projects", json={"name": slug, "slug": slug})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


async def _task(client, pid: str, **payload) -> dict:
    body = {"title": "T", "body": "orig body", "priority": 1, "labels": ["a"]}
    body.update(payload)
    resp = await client.post(f"/api/projects/{pid}/tasks", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestInvalidStatusIsRejected:
    async def test_patch_bogus_status_returns_422(self, client):
        pid = await _project(client, "bad-status-patch")
        t = await _task(client, pid)
        resp = await client.patch(
            f"/api/projects/{pid}/tasks/{t['id']}", json={"status": "bogus"}
        )
        assert resp.status_code == 422, resp.text

    async def test_list_tasks_bogus_status_returns_422(self, client):
        pid = await _project(client, "bad-status-list")
        await _task(client, pid)
        resp = await client.get(
            f"/api/projects/{pid}/tasks", params={"status": "bogus"}
        )
        assert resp.status_code == 422, resp.text

    @pytest.mark.parametrize("status", ("open", "claimed", "closed"))
    async def test_valid_status_still_works(self, client, status):
        pid = await _project(client, f"valid-status-{status}")
        t = await _task(client, pid)
        if status != "open":
            resp = await client.patch(
                f"/api/projects/{pid}/tasks/{t['id']}", json={"status": status}
            )
            assert resp.status_code == 200, resp.text
        resp = await client.get(
            f"/api/projects/{pid}/tasks", params={"status": status}
        )
        assert resp.status_code == 200, resp.text
        assert any(task["id"] == t["id"] for task in resp.json()["items"])


class TestInvalidDirectionIsRejected:
    async def test_list_relationships_bogus_direction_returns_422(self, client):
        pid = await _project(client, "bad-direction")
        t = await _task(client, pid)
        resp = await client.get(
            f"/api/projects/{pid}/tasks/{t['id']}/relationships",
            params={"direction": "x"},
        )
        assert resp.status_code == 422, resp.text

    @pytest.mark.parametrize("direction", ("from", "to"))
    async def test_valid_direction_still_works(self, client, direction):
        pid = await _project(client, f"valid-direction-{direction}")
        t = await _task(client, pid)
        resp = await client.get(
            f"/api/projects/{pid}/tasks/{t['id']}/relationships",
            params={"direction": direction},
        )
        assert resp.status_code == 200, resp.text
