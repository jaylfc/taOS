import pytest


@pytest.mark.asyncio
async def test_claim_emits_notification(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    t = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "T1"})).json()

    resp = await client.post(f"/api/projects/{pid}/tasks/{t['id']}/claim", json={"claimer_id": "agent-1"})
    assert resp.status_code == 200

    notifs = (await client.get("/api/notifications")).json()
    claimed = [n for n in notifs if n["source"] == "task.claimed"]
    assert len(claimed) == 1
    assert t["id"] in claimed[0]["message"]
    assert "agent-1" in claimed[0]["message"]


@pytest.mark.asyncio
async def test_close_emits_notification(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    t = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "T1"})).json()

    await client.post(f"/api/projects/{pid}/tasks/{t['id']}/claim", json={"claimer_id": "agent-1"})
    resp = await client.post(
        f"/api/projects/{pid}/tasks/{t['id']}/close",
        json={"closed_by": "agent-1", "reason": "done"},
    )
    assert resp.status_code == 200

    notifs = (await client.get("/api/notifications")).json()
    closed = [n for n in notifs if n["source"] == "task.closed"]
    assert len(closed) == 1
    assert t["id"] in closed[0]["message"]
    assert "agent-1" in closed[0]["message"]


@pytest.mark.asyncio
async def test_mute_suppresses_claim_notification(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    t = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "T1"})).json()

    store = client._transport.app.state.notifications
    await store.set_event_muted("task.claimed", True)

    resp = await client.post(f"/api/projects/{pid}/tasks/{t['id']}/claim", json={"claimer_id": "agent-1"})
    assert resp.status_code == 200

    notifs = (await client.get("/api/notifications")).json()
    claimed = [n for n in notifs if n["source"] == "task.claimed"]
    assert len(claimed) == 0
