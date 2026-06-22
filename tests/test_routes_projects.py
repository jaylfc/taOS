import pytest


@pytest.mark.asyncio
async def test_create_and_list_project(client):
    resp = await client.post("/api/projects", json={"name": "Alpha", "slug": "alpha", "description": "x"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"].startswith("prj-")
    assert body["slug"] == "alpha"

    resp = await client.get("/api/projects")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert any(p["slug"] == "alpha" for p in items)


@pytest.mark.asyncio
async def test_create_project_duplicate_slug_returns_409(client):
    await client.post("/api/projects", json={"name": "A", "slug": "dup"})
    resp = await client.post("/api/projects", json={"name": "B", "slug": "dup"})
    assert resp.status_code == 409


@pytest.mark.parametrize("bad_slug", ["../escape", "/abs", "with space", "UPPER", "x" * 64, "", "."])
@pytest.mark.asyncio
async def test_create_project_rejects_unsafe_slug(client, bad_slug):
    resp = await client.post("/api/projects", json={"name": "X", "slug": bad_slug})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_get_update_delete_project(client):
    resp = await client.post("/api/projects", json={"name": "A", "slug": "a"})
    pid = resp.json()["id"]

    resp = await client.get(f"/api/projects/{pid}")
    assert resp.status_code == 200

    resp = await client.patch(f"/api/projects/{pid}", json={"name": "A2"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "A2"

    resp = await client.post(f"/api/projects/{pid}/archive")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"

    resp = await client.delete(f"/api/projects/{pid}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"


@pytest.mark.asyncio
async def test_add_native_member(client):
    resp = await client.post("/api/projects", json={"name": "A", "slug": "a"})
    pid = resp.json()["id"]

    resp = await client.post(
        f"/api/projects/{pid}/members",
        json={"mode": "native", "agent_id": "agent-1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["member_kind"] == "native"
    assert body["member_id"] == "agent-1"

    resp = await client.get(f"/api/projects/{pid}/members")
    assert any(m["member_id"] == "agent-1" for m in resp.json()["items"])


@pytest.mark.asyncio
async def test_add_clone_member_with_memory_seed(client):
    resp = await client.post("/api/projects", json={"name": "A", "slug": "a"})
    pid = resp.json()["id"]

    resp = await client.post(
        f"/api/projects/{pid}/members",
        json={"mode": "clone", "source_agent_id": "agent-1", "clone_memory": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["member_kind"] == "clone"
    assert body["source_agent_id"] == "agent-1"
    assert body["memory_seed"] == "snapshot"
    assert body["member_id"] == "agent-1-a"


@pytest.mark.asyncio
async def test_add_clone_member_empty_memory(client):
    resp = await client.post("/api/projects", json={"name": "A", "slug": "a"})
    pid = resp.json()["id"]

    resp = await client.post(
        f"/api/projects/{pid}/members",
        json={"mode": "clone", "source_agent_id": "agent-1", "clone_memory": False},
    )
    assert resp.json()["memory_seed"] == "empty"


@pytest.mark.asyncio
async def test_remove_member(client):
    resp = await client.post("/api/projects", json={"name": "A", "slug": "a"})
    pid = resp.json()["id"]
    await client.post(f"/api/projects/{pid}/members", json={"mode": "native", "agent_id": "agent-1"})

    resp = await client.delete(f"/api/projects/{pid}/members/agent-1")
    assert resp.status_code == 200

    resp = await client.get(f"/api/projects/{pid}/members")
    assert resp.json()["items"] == []


@pytest.mark.asyncio
async def test_create_and_list_tasks(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]

    resp = await client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "T1", "body": "do it", "priority": 2},
    )
    assert resp.status_code == 200
    t = resp.json()
    assert t["id"].startswith("tsk-")

    resp = await client.get(f"/api/projects/{pid}/tasks")
    assert [x["id"] for x in resp.json()["items"]] == [t["id"]]


@pytest.mark.asyncio
async def test_ready_endpoint(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    a = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "A"})).json()
    b = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "B"})).json()
    await client.post(
        f"/api/projects/{pid}/tasks/{a['id']}/relationships",
        json={"to_task_id": b["id"], "kind": "blocks"},
    )
    resp = await client.get(f"/api/projects/{pid}/tasks/ready")
    assert [t["id"] for t in resp.json()["items"]] == [b["id"]]


@pytest.mark.asyncio
async def test_claim_blocked_while_agent_holds_active_task(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    a = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "A"})).json()
    b = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "B"})).json()

    assert (await client.post(f"/api/projects/{pid}/tasks/{a['id']}/claim", json={"claimer_id": "agent-1"})).status_code == 200

    resp = await client.post(f"/api/projects/{pid}/tasks/{b['id']}/claim", json={"claimer_id": "agent-1"})
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"] == "agent already holds an active task"
    assert body["held_task"] == a["id"]

    # releasing the first frees the agent to claim the second
    await client.post(f"/api/projects/{pid}/tasks/{a['id']}/release", json={"releaser_id": "agent-1"})
    assert (await client.post(f"/api/projects/{pid}/tasks/{b['id']}/claim", json={"claimer_id": "agent-1"})).status_code == 200


@pytest.mark.asyncio
async def test_claim_release_close(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    t = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "A"})).json()

    resp = await client.post(f"/api/projects/{pid}/tasks/{t['id']}/claim", json={"claimer_id": "agent-1"})
    assert resp.status_code == 200
    assert resp.json()["claimed_by"] == "agent-1"

    resp = await client.post(f"/api/projects/{pid}/tasks/{t['id']}/claim", json={"claimer_id": "agent-2"})
    assert resp.status_code == 409

    resp = await client.post(f"/api/projects/{pid}/tasks/{t['id']}/release", json={"releaser_id": "agent-1"})
    assert resp.status_code == 200

    await client.post(f"/api/projects/{pid}/tasks/{t['id']}/claim", json={"claimer_id": "agent-1"})
    resp = await client.post(
        f"/api/projects/{pid}/tasks/{t['id']}/close",
        json={"closed_by": "agent-1", "reason": "done"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"


@pytest.mark.asyncio
async def test_threaded_comments_route(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    t = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "T"})).json()

    resp = await client.post(
        f"/api/projects/{pid}/tasks/{t['id']}/comments",
        json={"body": "root", "author_id": "u"},
    )
    assert resp.status_code == 200
    c1 = resp.json()

    resp = await client.post(
        f"/api/projects/{pid}/tasks/{t['id']}/comments",
        json={"body": "reply", "author_id": "u2", "replies_to_comment_id": c1["id"]},
    )
    assert resp.json()["replies_to_comment_id"] == c1["id"]

    resp = await client.get(f"/api/projects/{pid}/tasks/{t['id']}/comments")
    assert len(resp.json()["items"]) == 2


@pytest.mark.asyncio
async def test_list_relationships_route(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    a = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "A"})).json()
    b = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "B"})).json()
    await client.post(
        f"/api/projects/{pid}/tasks/{a['id']}/relationships",
        json={"to_task_id": b["id"], "kind": "blocks"},
    )
    resp = await client.get(f"/api/projects/{pid}/tasks/{a['id']}/relationships")
    assert [r["to_task_id"] for r in resp.json()["items"]] == [b["id"]]


@pytest.mark.asyncio
async def test_activity_feed(client):
    resp = await client.post("/api/projects", json={"name": "A", "slug": "a"})
    pid = resp.json()["id"]
    await client.post(f"/api/projects/{pid}/members", json={"mode": "native", "agent_id": "agent-1"})
    await client.post(f"/api/projects/{pid}/tasks", json={"title": "T"})

    resp = await client.get(f"/api/projects/{pid}/activity")
    assert resp.status_code == 200
    kinds = [item["kind"] for item in resp.json()["items"]]
    assert "project.created" in kinds
    assert "member.added" in kinds
    assert "task.created" in kinds


@pytest.mark.asyncio
async def test_memory_search_route(client, monkeypatch):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]

    captured = {}

    async def fake_search(self, query, collection=None, tags=None, limit=10):
        captured["query"] = query
        captured["collection"] = collection
        captured["tags"] = tags
        return [{"path": "tasks/tsk-aaa.md", "score": 0.9, "title": "Draft"}]

    from tinyagentos.qmd_client import QmdClient
    monkeypatch.setattr(QmdClient, "search", fake_search, raising=False)

    resp = await client.get(f"/api/projects/{pid}/memory/search?q=draft")
    assert resp.status_code == 200
    assert resp.json()["items"][0]["path"] == "tasks/tsk-aaa.md"
    assert captured["collection"] == "project-a"
    assert "project:" + pid in captured["tags"]


@pytest.mark.asyncio
async def test_delete_project_tombstones_folder_and_archives_channels(client):
    resp = await client.post("/api/projects", json={"name": "A", "slug": "a"})
    pid = resp.json()["id"]
    slug = "a"

    channels_store = client._transport.app.state.chat_channels
    await channels_store.create_channel(
        name="alpha-room", type="group", created_by="u", project_id=pid,
    )

    resp = await client.delete(f"/api/projects/{pid}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deleted"

    root = client._transport.app.state.projects_root
    survivors = list(root.iterdir())
    assert any(p.name.startswith(f"{slug}.deleted-") for p in survivors)

    archived_channels = await channels_store.list_channels(archived=True)
    assert any(ch["project_id"] == pid for ch in archived_channels)


@pytest.mark.asyncio
async def test_add_member_idempotent_preserves_added_at(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    first = (await client.post(
        f"/api/projects/{pid}/members",
        json={"mode": "native", "agent_id": "agent-1"},
    )).json()
    await client.post(
        f"/api/projects/{pid}/members",
        json={"mode": "native", "agent_id": "agent-1"},
    )
    members = (await client.get(f"/api/projects/{pid}/members")).json()["items"]
    me = next(m for m in members if m["member_id"] == "agent-1")
    assert me["added_at"] == first["added_at"]


@pytest.mark.asyncio
async def test_archive_project_unknown_returns_404(client):
    resp = await client.post("/api/projects/prj-nope/archive")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_task_cross_project_returns_404(client):
    p1 = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    p2 = (await client.post("/api/projects", json={"name": "B", "slug": "b"})).json()["id"]
    t = (await client.post(f"/api/projects/{p1}/tasks", json={"title": "T"})).json()
    resp = await client.patch(
        f"/api/projects/{p2}/tasks/{t['id']}",
        json={"title": "hijacked"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_add_relationship_rejects_other_project_task(client):
    p1 = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    p2 = (await client.post("/api/projects", json={"name": "B", "slug": "b"})).json()["id"]
    a = (await client.post(f"/api/projects/{p1}/tasks", json={"title": "A"})).json()
    b = (await client.post(f"/api/projects/{p2}/tasks", json={"title": "B"})).json()
    resp = await client.post(
        f"/api/projects/{p1}/tasks/{a['id']}/relationships",
        json={"to_task_id": b["id"], "kind": "blocks"},
    )
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_add_comment_rejects_cross_task_reply(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    t1 = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "T1"})).json()
    t2 = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "T2"})).json()
    c1 = (await client.post(
        f"/api/projects/{pid}/tasks/{t1['id']}/comments",
        json={"body": "root", "author_id": "u"},
    )).json()
    resp = await client.post(
        f"/api/projects/{pid}/tasks/{t2['id']}/comments",
        json={"body": "reply", "author_id": "u", "replies_to_comment_id": c1["id"]},
    )
    assert resp.status_code in (400, 422)


@pytest.mark.asyncio
async def test_claim_release_close_reject_cross_project(client):
    p1 = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    p2 = (await client.post("/api/projects", json={"name": "B", "slug": "b"})).json()["id"]
    t = (await client.post(f"/api/projects/{p1}/tasks", json={"title": "T"})).json()

    resp = await client.post(
        f"/api/projects/{p2}/tasks/{t['id']}/claim",
        json={"claimer_id": "agent-x"},
    )
    assert resp.status_code == 404

    resp = await client.post(
        f"/api/projects/{p2}/tasks/{t['id']}/release",
        json={"releaser_id": "agent-x"},
    )
    assert resp.status_code == 404

    resp = await client.post(
        f"/api/projects/{p2}/tasks/{t['id']}/close",
        json={"closed_by": "agent-x"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_release_after_close_does_not_reopen(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    t = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "T"})).json()
    await client.post(f"/api/projects/{pid}/tasks/{t['id']}/claim", json={"claimer_id": "agent-1"})
    resp = await client.post(
        f"/api/projects/{pid}/tasks/{t['id']}/close",
        json={"closed_by": "agent-1"},
    )
    assert resp.json()["status"] == "closed"

    resp = await client.post(
        f"/api/projects/{pid}/tasks/{t['id']}/release",
        json={"releaser_id": "agent-1"},
    )
    assert resp.status_code == 409

    resp = await client.get(f"/api/projects/{pid}/tasks/{t['id']}")
    assert resp.json()["status"] == "closed"


@pytest.mark.asyncio
async def test_comments_and_relationships_reject_wrong_project(client):
    p1 = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    p2 = (await client.post("/api/projects", json={"name": "B", "slug": "b"})).json()["id"]
    t = (await client.post(f"/api/projects/{p1}/tasks", json={"title": "T"})).json()

    resp = await client.post(
        f"/api/projects/{p2}/tasks/{t['id']}/comments",
        json={"body": "x", "author_id": "u"},
    )
    assert resp.status_code == 404

    resp = await client.get(f"/api/projects/{p2}/tasks/{t['id']}/comments")
    assert resp.status_code == 404

    resp = await client.get(f"/api/projects/{p2}/tasks/{t['id']}/relationships")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_task_unknown_project_returns_404(client):
    resp = await client.post("/api/projects/prj-nope/tasks", json={"title": "T"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_task_rejects_cross_project_parent(client):
    p1 = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    p2 = (await client.post("/api/projects", json={"name": "B", "slug": "b"})).json()["id"]
    parent = (await client.post(f"/api/projects/{p1}/tasks", json={"title": "P"})).json()
    resp = await client.post(
        f"/api/projects/{p2}/tasks",
        json={"title": "child", "parent_task_id": parent["id"]},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_create_project_duplicate_slug_via_store_concurrent(client):
    # Simulates the race where two creates pass any pre-check and both reach INSERT.
    store = client._transport.app.state.project_store
    await store.create_project(name="A", slug="race", created_by="u")
    with pytest.raises(ValueError, match="slug already used"):
        await store.create_project(name="B", slug="race", created_by="u")
