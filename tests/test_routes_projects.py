import logging

import pytest
from httpx import ASGITransport, AsyncClient


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


@pytest.mark.asyncio
async def test_task_context_route_shape(client):
    pid = (await client.post(
        "/api/projects", json={"name": "Alpha", "slug": "alpha", "description": "Ship v2"},
    )).json()["id"]
    root = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "Epic"})).json()
    leaf = (await client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "Subtask", "parent_task_id": root["id"]},
    )).json()
    blocker = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "Blocker"})).json()
    await client.post(
        f"/api/projects/{pid}/tasks/{leaf['id']}/relationships",
        json={"to_task_id": blocker["id"], "kind": "blocks"},
    )

    resp = await client.get(f"/api/projects/tasks/{leaf['id']}/context")
    assert resp.status_code == 200
    body = resp.json()
    assert body["project"]["id"] == pid
    assert body["project"]["name"] == "Alpha"
    assert body["project"]["description"] == "Ship v2"
    assert [a["id"] for a in body["ancestry"]] == [root["id"]]
    assert [b["id"] for b in body["blockers"]] == [blocker["id"]]
    assert body["is_blocked"] is True


@pytest.mark.asyncio
async def test_task_context_route_unknown_task_returns_404(client):
    resp = await client.get("/api/projects/tasks/tsk-nope/context")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Slice 1: project elements + task element tags
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_and_list_elements(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]

    resp = await client.post(
        f"/api/projects/{pid}/elements",
        json={"name": "Website", "type": "website", "description": "the site"},
    )
    assert resp.status_code == 200, resp.text
    el = resp.json()
    assert el["id"].startswith("elm-")
    assert el["slug"] == "website"
    assert el["type"] == "website"
    assert el["description"] == "the site"

    # default slug from name when omitted
    resp2 = await client.post(
        f"/api/projects/{pid}/elements", json={"name": "Designs"}
    )
    assert resp2.status_code == 200, resp2.text
    assert resp2.json()["slug"] == "designs"
    assert resp2.json()["type"] == "generic"

    listing = await client.get(f"/api/projects/{pid}/elements")
    assert listing.status_code == 200
    items = {e["slug"]: e for e in listing.json()["items"]}
    assert set(items) == {"website", "designs"}
    # The list endpoint carries counts (open/total tasks, canvas items).
    assert items["website"]["open_tasks"] == 0
    assert items["website"]["total_tasks"] == 0
    assert items["website"]["canvas_items"] == 0

    single = await client.get(f"/api/projects/{pid}/elements/{el['id']}")
    assert single.status_code == 200
    assert single.json()["name"] == "Website"


@pytest.mark.asyncio
async def test_element_duplicate_slug_returns_409(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    await client.post(f"/api/projects/{pid}/elements", json={"name": "Website", "slug": "website"})
    resp = await client.post(
        f"/api/projects/{pid}/elements", json={"name": "Website Two", "slug": "website"}
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_get_update_archive_delete_element(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    el = (await client.post(
        f"/api/projects/{pid}/elements", json={"name": "Website", "type": "website"}
    )).json()

    resp = await client.patch(
        f"/api/projects/{pid}/elements/{el['id']}",
        json={"name": "Site", "description": "d"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Site"

    resp = await client.post(f"/api/projects/{pid}/elements/{el['id']}/archive")
    assert resp.status_code == 200
    assert resp.json()["archived_at"] is not None

    resp = await client.delete(f"/api/projects/{pid}/elements/{el['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert (await client.get(f"/api/projects/{pid}/elements/{el['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_create_element_assignee_must_be_member(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    resp = await client.post(
        f"/api/projects/{pid}/elements",
        json={"name": "Website", "assignee_id": "agent-not-a-member"},
    )
    assert resp.status_code == 400
    await client.post(f"/api/projects/{pid}/members", json={"mode": "native", "agent_id": "agent-1"})
    resp = await client.post(
        f"/api/projects/{pid}/elements",
        json={"name": "Website", "assignee_id": "agent-1"},
    )
    assert resp.status_code == 200
    assert resp.json()["assignee_id"] == "agent-1"


@pytest.mark.asyncio
async def test_element_delete_with_tagged_tasks_returns_409(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    el = (await client.post(f"/api/projects/{pid}/elements", json={"name": "Website"})).json()
    t = (await client.post(
        f"/api/projects/{pid}/tasks", json={"title": "T", "element_id": el["id"]}
    )).json()

    resp = await client.delete(f"/api/projects/{pid}/elements/{el['id']}")
    assert resp.status_code == 409
    body = resp.json()
    assert body["total_tasks"] == 1
    assert body["open_tasks"] == 1
    # The task (and element) survive the refused delete.
    assert (await client.get(f"/api/projects/{pid}/tasks/{t['id']}")).status_code == 200
    assert (await client.get(f"/api/projects/{pid}/elements/{el['id']}")).status_code == 200


@pytest.mark.asyncio
async def test_element_delete_untag_mode(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    el = (await client.post(f"/api/projects/{pid}/elements", json={"name": "Website"})).json()
    t = (await client.post(
        f"/api/projects/{pid}/tasks", json={"title": "T", "element_id": el["id"]}
    )).json()

    resp = await client.delete(
        f"/api/projects/{pid}/elements/{el['id']}?mode=untag"
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    # Task stays but its tag is cleared to project-level.
    cleared = (await client.get(f"/api/projects/{pid}/tasks/{t['id']}")).json()
    assert cleared["element_id"] is None
    assert (await client.get(f"/api/projects/{pid}/elements/{el['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_create_task_with_element_id(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    el = (await client.post(f"/api/projects/{pid}/elements", json={"name": "Website"})).json()

    resp = await client.post(
        f"/api/projects/{pid}/tasks", json={"title": "T", "element_id": el["id"]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["element_id"] == el["id"]

    listing = await client.get(f"/api/projects/{pid}/elements")
    assert listing.json()["items"][0]["open_tasks"] == 1


@pytest.mark.asyncio
async def test_create_task_with_invalid_element_id_400(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    resp = await client.post(
        f"/api/projects/{pid}/tasks", json={"title": "T", "element_id": "elm-nope"}
    )
    assert resp.status_code == 400
    # An archived element is not a valid tag target either.
    el = (await client.post(f"/api/projects/{pid}/elements", json={"name": "Website"})).json()
    await client.post(f"/api/projects/{pid}/elements/{el['id']}/archive")
    resp = await client.post(
        f"/api/projects/{pid}/tasks", json={"title": "T2", "element_id": el["id"]}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_list_tasks_filter_by_element_and_none(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    el = (await client.post(f"/api/projects/{pid}/elements", json={"name": "Website"})).json()
    tagged = (await client.post(
        f"/api/projects/{pid}/tasks", json={"title": "tagged", "element_id": el["id"]}
    )).json()
    untagged = (await client.post(
        f"/api/projects/{pid}/tasks", json={"title": "general"}
    )).json()

    all_items = {t["id"] for t in (await client.get(f"/api/projects/{pid}/tasks")).json()["items"]}
    assert all_items == {tagged["id"], untagged["id"]}

    by_element = (await client.get(
        f"/api/projects/{pid}/tasks", params={"element_id": el["id"]}
    )).json()["items"]
    assert [t["id"] for t in by_element] == [tagged["id"]]

    none_items = (await client.get(
        f"/api/projects/{pid}/tasks", params={"element_id": "none"}
    )).json()["items"]
    assert [t["id"] for t in none_items] == [untagged["id"]]


@pytest.mark.asyncio
async def test_update_task_move_element_and_clear(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    web = (await client.post(f"/api/projects/{pid}/elements", json={"name": "Website"})).json()
    design = (await client.post(f"/api/projects/{pid}/elements", json={"name": "Designs"})).json()
    t = (await client.post(
        f"/api/projects/{pid}/tasks", json={"title": "T", "element_id": web["id"]}
    )).json()

    # Move to another element.
    resp = await client.patch(
        f"/api/projects/{pid}/tasks/{t['id']}",
        json={"element_id": design["id"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["element_id"] == design["id"]

    # Clear to project-level via the "none" sentinel.
    resp = await client.patch(
        f"/api/projects/{pid}/tasks/{t['id']}", json={"element_id": "none"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["element_id"] is None

    # Omitting element_id leaves the tag untouched.
    resp = await client.patch(
        f"/api/projects/{pid}/tasks/{t['id']}", json={"title": "renamed"}
    )
    assert resp.status_code == 200
    assert resp.json()["element_id"] is None


@pytest.mark.asyncio
async def test_ready_tasks_filter_by_element(client):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    el = (await client.post(f"/api/projects/{pid}/elements", json={"name": "Website"})).json()
    tagged = (await client.post(
        f"/api/projects/{pid}/tasks", json={"title": "tagged", "element_id": el["id"]}
    )).json()
    await client.post(f"/api/projects/{pid}/tasks", json={"title": "general"})

    by_element = (await client.get(
        f"/api/projects/{pid}/tasks/ready", params={"element_id": el["id"]}
    )).json()["items"]
    assert [t["id"] for t in by_element] == [tagged["id"]]

    none_items = (await client.get(
        f"/api/projects/{pid}/tasks/ready", params={"element_id": "none"}
    )).json()["items"]
    assert all(t["element_id"] is None for t in none_items)
    assert len(none_items) == 1


# ---------------------------------------------------------------------------
# Slice 6 (D7): the Lead designation is an exclusive, project-level pointer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_lead_exclusive_leaves_only_last(client):
    """Setting lead B after lead A leaves only B; the pointer holds one lead."""
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "lead-excl"})).json()["id"]
    await client.post(f"/api/projects/{pid}/members", json={"mode": "native", "agent_id": "agent-a"})
    await client.post(f"/api/projects/{pid}/members", json={"mode": "native", "agent_id": "agent-b"})

    r1 = await client.patch(f"/api/projects/{pid}/lead", json={"member_id": "agent-a"})
    assert r1.status_code == 200
    assert r1.json()["lead_member_id"] == "agent-a"

    r2 = await client.patch(f"/api/projects/{pid}/lead", json={"member_id": "agent-b"})
    assert r2.status_code == 200
    assert r2.json()["lead_member_id"] == "agent-b"

    # The pointer cannot hold two leads; only the last one remains.
    proj = (await client.get(f"/api/projects/{pid}")).json()
    assert proj["lead_member_id"] == "agent-b"


@pytest.mark.asyncio
async def test_set_lead_non_member_returns_404(client):
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "lead-404"})).json()["id"]
    await client.post(f"/api/projects/{pid}/members", json={"mode": "native", "agent_id": "agent-a"})

    r = await client.patch(f"/api/projects/{pid}/lead", json={"member_id": "not-a-member"})
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_clear_lead_to_null(client):
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "lead-null"})).json()["id"]
    await client.post(f"/api/projects/{pid}/members", json={"mode": "native", "agent_id": "agent-a"})

    r = await client.patch(f"/api/projects/{pid}/lead", json={"member_id": "agent-a"})
    assert r.status_code == 200
    assert r.json()["lead_member_id"] == "agent-a"
    assert (await client.get(f"/api/projects/{pid}")).json()["lead_member_id"] == "agent-a"

    r = await client.patch(f"/api/projects/{pid}/lead", json={"member_id": None})
    assert r.status_code == 200
    assert r.json()["lead_member_id"] is None
    assert (await client.get(f"/api/projects/{pid}")).json()["lead_member_id"] is None


@pytest.mark.asyncio
async def test_set_lead_requires_session(app, client):
    """The lead route is session-only: an unauthenticated request is rejected."""
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "lead-auth"})).json()["id"]
    await client.post(f"/api/projects/{pid}/members", json={"mode": "native", "agent_id": "agent-a"})

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as anon:
        r = await anon.patch(f"/api/projects/{pid}/lead", json={"member_id": "agent-a"})
    assert r.status_code in (401, 403)


@pytest.mark.asyncio
async def test_old_per_member_lead_route_removed(client):
    """The retired per-member lead route is gone (replaced by the project pointer)."""
    pid = (await client.post("/api/projects", json={"name": "P", "slug": "lead-old"})).json()["id"]
    await client.post(f"/api/projects/{pid}/members", json={"mode": "native", "agent_id": "agent-a"})

    r = await client.patch(
        f"/api/projects/{pid}/members/agent-a/lead", json={"is_lead": True}
    )
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# tsk-kqzpjt: observation phase for silent key-drops on task request models.
# Unknown keys are accepted (extra="allow") but logged as a warning. These
# tests pin: (a) a wrong key still returns 200 and emits the warning naming
# the unknown key; (b) the correct key logs nothing and persists; (c) create
# with a wrong key (tags) emits the warning naming tags.
# ---------------------------------------------------------------------------

_LOGGER = "tinyagentos.routes.projects"


@pytest.mark.asyncio
async def test_close_with_unknown_key_warns_and_succeeds(client, caplog):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    t = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "A"})).json()

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        resp = await client.post(
            f"/api/projects/{pid}/tasks/{t['id']}/close",
            json={"closed_by": "agent-1", "close_reason": "done"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"
    warns = [r for r in caplog.records if "unknown keys" in r.message]
    assert warns, "expected a warning for unknown key close_reason"
    msg = warns[0].message
    assert "CloseIn" in msg
    assert "close_reason" in msg


@pytest.mark.asyncio
async def test_close_with_correct_key_no_warning_and_persists(client, caplog):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]
    t = (await client.post(f"/api/projects/{pid}/tasks", json={"title": "A"})).json()

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        resp = await client.post(
            f"/api/projects/{pid}/tasks/{t['id']}/close",
            json={"closed_by": "agent-1", "reason": "done"},
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "closed"
    assert resp.json()["close_reason"] == "done"
    warns = [r for r in caplog.records if "unknown keys" in r.message]
    assert not warns, "no warning expected for the correct key reason"


@pytest.mark.asyncio
async def test_create_with_unknown_key_tags_warns(client, caplog):
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]

    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        resp = await client.post(
            f"/api/projects/{pid}/tasks",
            json={"title": "T", "tags": ["bug"]},
        )
    assert resp.status_code == 422
    err = resp.json()
    assert "extra" in str(err).lower() or "validation" in str(err).lower() or "unknown" in str(err).lower()


@pytest.mark.asyncio
async def test_create_with_description_instead_of_body_returns_422(client):
    """POST with 'description' instead of 'body' must be rejected with 422.

    This catches the defect where sending "description" (a field that silently
    vanishes) resulted in a card with an empty body - the only channel that
    reaches a lane. The rejection here comes from ``extra="forbid"`` (unknown
    key), before any model validator runs.
    """
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]

    resp = await client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "T", "description": "do the thing", "labels": ["claimable"]},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "description" in str(body).lower() or "extra" in str(body).lower()


@pytest.mark.asyncio
async def test_create_claimable_with_empty_body_returns_422(client):
    """A claimable card with no body must be rejected by the model validator.

    The payload contains ONLY declared fields, so ``extra="forbid"`` cannot
    reject it first — this exercises ``_assert_body_for_claimable`` itself
    (deleting that validator makes this test fail).
    """
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "a"})).json()["id"]

    for payload in (
        {"title": "T", "labels": ["claimable"]},
        {"title": "T", "labels": ["Claimable "], "body": "   "},
    ):
        resp = await client.post(f"/api/projects/{pid}/tasks", json=payload)
        assert resp.status_code == 422, payload
        assert "claimable" in str(resp.json()).lower()

    resp = await client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "T", "labels": ["claimable"], "body": "do the thing"},
    )
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# tsk-wkah3z: ready view honour blocked-on:<id> labels and the limit param
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ready_excludes_open_blocked_on_label(client):
    """Arm A: a task labelled blocked-on:<id> with <id> OPEN is excluded.

    Hits the real HTTP route with the real auth dependency (the shared
    ``client`` fixture signs in as the test admin via the session cookie
    path), not a fixture that bypasses the router.
    """
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "bo"})).json()["id"]
    blocker = (await client.post(
        f"/api/projects/{pid}/tasks", json={"title": "Blocker"}
    )).json()
    blocked = (await client.post(
        f"/api/projects/{pid}/tasks", json={"title": "Blocked"}
    )).json()

    patch_resp = await client.patch(
        f"/api/projects/{pid}/tasks/{blocked['id']}",
        json={"labels": [f"blocked-on:{blocker['id']}"]},
    )
    assert patch_resp.status_code == 200, patch_resp.text
    assert f"blocked-on:{blocker['id']}" in patch_resp.json()["labels"]

    resp = await client.get(f"/api/projects/{pid}/tasks/ready")
    ids = [t["id"] for t in resp.json()["items"]]
    assert blocked["id"] not in ids, (
        f"ready returned {blocked['id']} carrying an open blocked-on:{blocker['id']} label"
    )
    assert blocker["id"] in ids

    close_resp = await client.post(
        f"/api/projects/{pid}/tasks/{blocker['id']}/close",
        json={"closed_by": "admin", "reason": "done"},
    )
    assert close_resp.status_code == 200, close_resp.text

    resp_after = await client.get(f"/api/projects/{pid}/tasks/ready")
    ids_after = [t["id"] for t in resp_after.json()["items"]]
    assert blocked["id"] in ids_after, (
        "un-blocking direction: closing the blocker must re-surface the blocked task"
    )


@pytest.mark.asyncio
async def test_ready_stale_blocked_on_label_is_ready(client):
    """Arm B: a blocked-on:<id> label pointing at a CLOSED task is stale -> ready."""
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "stale"})).json()["id"]
    gone = (await client.post(
        f"/api/projects/{pid}/tasks", json={"title": "AlreadyClosed"}
    )).json()
    close_resp = await client.post(
        f"/api/projects/{pid}/tasks/{gone['id']}/close",
        json={"closed_by": "admin"},
    )
    assert close_resp.status_code == 200, close_resp.text

    stale = (await client.post(
        f"/api/projects/{pid}/tasks",
        json={"title": "Stale", "labels": [f"blocked-on:{gone['id']}"]},
    )).json()
    assert f"blocked-on:{gone['id']}" in stale["labels"]

    resp = await client.get(f"/api/projects/{pid}/tasks/ready")
    ids = [t["id"] for t in resp.json()["items"]]
    assert stale["id"] in ids, (
        f"stale blocked-on label (target {gone['id']} is closed) must not exclude {stale['id']}"
    )


@pytest.mark.asyncio
async def test_ready_limit_param_honoured_and_clamped(client):
    """Arm C: ?limit is honoured, 0/-1 clamp to the floor, >500 clamps to 500."""
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "lim"})).json()["id"]
    for i in range(10):
        r = await client.post(
            f"/api/projects/{pid}/tasks", json={"title": f"T{i}"}
        )
        assert r.status_code == 200

    five = await client.get(f"/api/projects/{pid}/tasks/ready", params={"limit": 5})
    assert five.status_code == 200
    assert len(five.json()["items"]) == 5

    zero = await client.get(f"/api/projects/{pid}/tasks/ready", params={"limit": 0})
    assert zero.status_code == 200
    assert len(zero.json()["items"]) >= 1, "?limit=0 must NOT return unbounded / 0 items"

    neg = await client.get(f"/api/projects/{pid}/tasks/ready", params={"limit": -1})
    assert neg.status_code == 200
    assert len(neg.json()["items"]) >= 1, "?limit=-1 must NOT return unbounded"

    huge = await client.get(
        f"/api/projects/{pid}/tasks/ready", params={"limit": 99999}
    )
    assert huge.status_code == 200
    assert len(huge.json()["items"]) <= 500


@pytest.mark.asyncio
async def test_ready_limit_clamp_500_enforced_with_501_tasks(client):
    """tsk-cifqsh finding 1: with 501+ ready tasks, ?limit=99999 must clamp to 500.

    Previous test only created 10 tasks, so an unclamped limit also returned
    10 and the assertion passed without enforcing the cap. Seed 501 ready
    tasks and assert the upper cap returns exactly 500.
    """
    pid = (await client.post("/api/projects", json={"name": "A", "slug": "cap"})).json()["id"]
    for i in range(501):
        r = await client.post(
            f"/api/projects/{pid}/tasks", json={"title": f"T{i}"}
        )
        assert r.status_code == 200, r.text

    huge = await client.get(
        f"/api/projects/{pid}/tasks/ready", params={"limit": 99999}
    )
    assert huge.status_code == 200
    assert len(huge.json()["items"]) == 500, (
        f"?limit=99999 must clamp to 500 even when more than 500 ready tasks exist, "
        f"got {len(huge.json()['items'])}"
    )


@pytest.mark.asyncio
async def test_ready_blocked_on_label_does_not_match_across_projects(client):
    """tsk-cifqsh finding 2: a blocked-on:<id> label must only match same-project tasks.

    A task in project A labelled blocked-on:<blocker-in-project-B> must remain
    ready; the label is meaningless across project boundaries. The schema view
    join (and its migration twin) must constrain on ``bt.project_id = t.project_id``.
    """
    pid_a = (await client.post("/api/projects", json={"name": "A", "slug": "xpa"})).json()["id"]
    pid_b = (await client.post("/api/projects", json={"name": "B", "slug": "xpb"})).json()["id"]

    blocker_b = (await client.post(
        f"/api/projects/{pid_b}/tasks", json={"title": "BlockerInB"}
    )).json()

    foreign_labeled = (await client.post(
        f"/api/projects/{pid_a}/tasks",
        json={"title": "ForeignLabeled", "labels": [f"blocked-on:{blocker_b['id']}"]},
    )).json()
    assert f"blocked-on:{blocker_b['id']}" in foreign_labeled["labels"]

    resp = await client.get(f"/api/projects/{pid_a}/tasks/ready")
    ids = [t["id"] for t in resp.json()["items"]]
    assert foreign_labeled["id"] in ids, (
        f"cross-project blocked-on:{blocker_b['id']} label must not exclude "
        f"{foreign_labeled['id']} in project {pid_a}"
    )

