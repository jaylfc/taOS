"""PATCH /api/projects/{pid}/tasks/{tid} must never assert a write it did not make.

tsk-5xq2mw: the route answered 200 with the task JSON for payloads it silently
dropped, so every caller check short of a fresh read passed:

  * an explicit null clear -- the board sends ``{"assignee_id": null}`` when a
    card is dragged to the "Unassigned" lane and ``{"parent_task_id": null}``
    for the "Orphans" lane (desktop/src/apps/ProjectsApp/board/boardDnd.ts) --
    was read as "field omitted, leave unchanged";
  * a field the route does not accept at all (a typo, or a read-only column
    such as ``id`` / ``created_by`` / ``claimed_by``) was accepted with 200.

Every PATCH here is verified by a re-read, and the fields the route will not
write are pinned to 422 rather than a silent 200.
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


async def _reread(client, pid: str, tid: str) -> dict:
    resp = await client.get(f"/api/projects/{pid}/tasks/{tid}")
    assert resp.status_code == 200, resp.text
    return resp.json()


class TestExplicitNullClears:
    async def test_clear_assignee_persists(self, client):
        pid = await _project(client, "clr-assignee")
        t = await _task(client, pid, assignee_id="worker-1")
        resp = await client.patch(
            f"/api/projects/{pid}/tasks/{t['id']}", json={"assignee_id": None}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["assignee_id"] is None
        assert (await _reread(client, pid, t["id"]))["assignee_id"] is None

    async def test_clear_parent_persists(self, client):
        pid = await _project(client, "clr-parent")
        parent = await _task(client, pid, title="Parent")
        child = await _task(client, pid, title="Child", parent_task_id=parent["id"])
        assert child["parent_task_id"] == parent["id"]
        resp = await client.patch(
            f"/api/projects/{pid}/tasks/{child['id']}", json={"parent_task_id": None}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["parent_task_id"] is None
        assert (await _reread(client, pid, child["id"]))["parent_task_id"] is None

    async def test_clear_element_persists(self, client):
        pid = await _project(client, "clr-element")
        el = await client.post(
            f"/api/projects/{pid}/elements", json={"name": "E"}
        )
        assert el.status_code == 200, el.text
        eid = el.json()["id"]
        t = await _task(client, pid, element_id=eid)
        assert t["element_id"] == eid
        resp = await client.patch(
            f"/api/projects/{pid}/tasks/{t['id']}", json={"element_id": None}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["element_id"] is None
        assert (await _reread(client, pid, t["id"]))["element_id"] is None

    async def test_element_none_sentinel_still_clears(self, client):
        """Regression pin: the documented ``"none"`` string keeps working."""
        pid = await _project(client, "clr-element-str")
        el = await client.post(
            f"/api/projects/{pid}/elements", json={"name": "E"}
        )
        eid = el.json()["id"]
        t = await _task(client, pid, element_id=eid)
        resp = await client.patch(
            f"/api/projects/{pid}/tasks/{t['id']}", json={"element_id": "none"}
        )
        assert resp.status_code == 200, resp.text
        assert (await _reread(client, pid, t["id"]))["element_id"] is None


class TestUnwritableFieldsAreRejected:
    @pytest.mark.parametrize(
        "payload",
        [
            {"despcription": "typo"},
            {"id": "tsk-spoofed"},
            {"created_by": "someone-else"},
            {"claimed_by": "someone-else"},
            {"project_id": "other-project"},
        ],
    )
    async def test_field_the_route_cannot_write_is_422(self, client, payload):
        pid = await _project(client, f"rej-{next(iter(payload))}")
        t = await _task(client, pid)
        resp = await client.patch(
            f"/api/projects/{pid}/tasks/{t['id']}", json=payload
        )
        assert resp.status_code == 422, resp.text
        after = await _reread(client, pid, t["id"])
        for field in ("id", "created_by", "claimed_by", "project_id"):
            assert after[field] == t[field]

    @pytest.mark.parametrize(
        "field", ["title", "body", "priority", "labels", "status"]
    )
    async def test_null_on_a_non_nullable_field_is_422(self, client, field):
        """A null for a column that cannot hold one is a caller mistake, not a
        no-op: answering 200 with the unchanged task hides it."""
        pid = await _project(client, f"null-{field}")
        t = await _task(client, pid)
        resp = await client.patch(
            f"/api/projects/{pid}/tasks/{t['id']}", json={field: None}
        )
        assert resp.status_code == 422, resp.text
        assert (await _reread(client, pid, t["id"]))[field] == t[field]


class TestAcceptedFieldsSurviveAReRead:
    @pytest.mark.parametrize(
        "field,value",
        [
            ("title", "renamed"),
            ("body", "revised body"),
            ("priority", 7),
            ("labels", ["bug", "urgent"]),
            ("status", "closed"),
            ("assignee_id", "worker-2"),
        ],
    )
    async def test_patch_persists(self, client, field, value):
        pid = await _project(client, f"persist-{field}")
        t = await _task(client, pid)
        resp = await client.patch(
            f"/api/projects/{pid}/tasks/{t['id']}", json={field: value}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()[field] == value
        assert (await _reread(client, pid, t["id"]))[field] == value

    async def test_omitted_fields_stay_unchanged(self, client):
        pid = await _project(client, "persist-omit")
        t = await _task(client, pid, assignee_id="worker-1")
        resp = await client.patch(
            f"/api/projects/{pid}/tasks/{t['id']}", json={"title": "renamed"}
        )
        assert resp.status_code == 200, resp.text
        after = await _reread(client, pid, t["id"])
        assert after["title"] == "renamed"
        assert after["body"] == t["body"]
        assert after["priority"] == t["priority"]
        assert after["labels"] == t["labels"]
        assert after["assignee_id"] == "worker-1"
