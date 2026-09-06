"""Tests for board audit-log wiring into the project task lifecycle (#105).

Every status transition (create -> claim -> release -> close -> reopen) must
append an append-only audit event, readable via the task audit endpoint, in
insertion order with accurate from/to status.
"""
import pytest


async def _make_task(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    tid = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "T1"})).json()["id"]
    return pid, tid


@pytest.mark.asyncio
async def test_create_records_open_event(client):
    pid, tid = await _make_task(client)
    resp = await client.get(f"/api/projects/{pid}/tasks/{tid}/audit")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert len(events) == 1
    assert events[0]["event"] == "task.created"
    assert events[0]["from_status"] is None
    assert events[0]["to_status"] == "open"


@pytest.mark.asyncio
async def test_full_lifecycle_is_audited_in_order(client):
    pid, tid = await _make_task(client)
    await client.post(f"/api/projects/{pid}/tasks/{tid}/claim", json={"claimer_id": "agent-1"})
    await client.post(f"/api/projects/{pid}/tasks/{tid}/release", json={"releaser_id": "agent-1"})
    await client.post(f"/api/projects/{pid}/tasks/{tid}/claim", json={"claimer_id": "agent-2"})
    await client.post(
        f"/api/projects/{pid}/tasks/{tid}/close",
        json={"closed_by": "agent-2", "reason": "done"},
    )
    await client.post(f"/api/projects/{pid}/tasks/{tid}/reopen", json={"reopened_by": "user"})

    events = (await client.get(f"/api/projects/{pid}/tasks/{tid}/audit")).json()["events"]
    transitions = [(e["event"], e["from_status"], e["to_status"]) for e in events]
    assert transitions == [
        ("task.created", None, "open"),
        ("task.claimed", "open", "claimed"),
        ("task.released", "claimed", "open"),
        ("task.claimed", "open", "claimed"),
        ("task.closed", "claimed", "closed"),
        ("task.reopened", "closed", "open"),
    ]
    # The closing actor is captured.
    closed = next(e for e in events if e["event"] == "task.closed")
    assert closed["actor"] == "agent-2"


@pytest.mark.asyncio
async def test_audit_endpoint_404s_unknown_task(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    resp = await client.get(f"/api/projects/{pid}/tasks/tsk-nope/audit")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_failed_transition_is_not_audited(client):
    """A no-op transition (reopen on a non-closed task) records nothing."""
    pid, tid = await _make_task(client)
    resp = await client.post(
        f"/api/projects/{pid}/tasks/{tid}/reopen", json={"reopened_by": "user"}
    )
    assert resp.status_code == 409
    events = (await client.get(f"/api/projects/{pid}/tasks/{tid}/audit")).json()["events"]
    # Only the creation event; the failed reopen left no trace.
    assert [e["event"] for e in events] == ["task.created"]


@pytest.mark.asyncio
async def test_project_audit_feed_is_scoped(client):
    """The project-wide feed returns only that project's events, newest first."""
    p1 = (await client.post("/api/projects", json={"name": "P1", "slug": "p1"})).json()["id"]
    p2 = (await client.post("/api/projects", json={"name": "P2", "slug": "p2"})).json()["id"]
    t1 = (await client.post(f"/api/projects/{p1}/tasks", json={"title": "A"})).json()["id"]
    await client.post(f"/api/projects/{p2}/tasks", json={"title": "B"})
    await client.post(f"/api/projects/{p1}/tasks/{t1}/claim", json={"claimer_id": "agent-1"})

    feed = (await client.get(f"/api/projects/{p1}/audit")).json()["events"]
    events = [e["event"] for e in feed]
    # Newest first: claim then create; nothing from p2.
    assert events == ["task.claimed", "task.created"]
    assert all(e["project_id"] == p1 for e in feed)


@pytest.mark.asyncio
async def test_project_audit_feed_limit_clamped(client):
    p1 = (await client.post("/api/projects", json={"name": "P1", "slug": "p1"})).json()["id"]
    await client.post(f"/api/projects/{p1}/tasks", json={"title": "A"})
    await client.post(f"/api/projects/{p1}/tasks", json={"title": "B"})
    feed = (await client.get(f"/api/projects/{p1}/audit", params={"limit": 1})).json()["events"]
    assert len(feed) == 1
