import pytest


@pytest.mark.asyncio
async def test_patch_accepts_parent_task_id(client):
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "p1"})).json()["id"]
    parent = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "Parent"})).json()
    child = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "Child"})).json()

    resp = await client.patch(
        f"/api/projects/{pid}/tasks/{child['id']}",
        json={"parent_task_id": parent["id"]},
    )
    assert resp.status_code == 200
    assert resp.json()["parent_task_id"] == parent["id"]


@pytest.mark.asyncio
async def test_patch_rejects_self_parent(client):
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "p2"})).json()["id"]
    t = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "T"})).json()

    resp = await client.patch(
        f"/api/projects/{pid}/tasks/{t['id']}",
        json={"parent_task_id": t["id"]},
    )
    assert resp.status_code == 400
    body = resp.json()
    assert "cycle" in body.get("error", "").lower() or "self" in body.get("error", "").lower()


@pytest.mark.asyncio
async def test_patch_rejects_cross_project_parent(client):
    p1 = (await client.post("/api/projects", json={"name": "P1", "slug": "cp1"})).json()["id"]
    p2 = (await client.post("/api/projects", json={"name": "P2", "slug": "cp2"})).json()["id"]
    t1 = (await client.post(f"/api/projects/{p1}/tasks", json={"title": "T1"})).json()
    t2 = (await client.post(f"/api/projects/{p2}/tasks", json={"title": "T2"})).json()

    resp = await client.patch(
        f"/api/projects/{p1}/tasks/{t1['id']}",
        json={"parent_task_id": t2["id"]},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_patch_rejects_indirect_cycle(client):
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "ic1"})).json()["id"]
    a = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "A"})).json()
    b = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "B"})).json()
    c = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "C"})).json()

    # chain: A -> B -> C
    await client.patch(
        f"/api/projects/{pid}/tasks/{a['id']}",
        json={"parent_task_id": b["id"]},
    )
    await client.patch(
        f"/api/projects/{pid}/tasks/{b['id']}",
        json={"parent_task_id": c["id"]},
    )

    # now try to set C's parent to A — would create a cycle
    resp = await client.patch(
        f"/api/projects/{pid}/tasks/{c['id']}",
        json={"parent_task_id": a["id"]},
    )
    assert resp.status_code == 400
