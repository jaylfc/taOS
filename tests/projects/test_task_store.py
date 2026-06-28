import pytest
import pytest_asyncio

from tinyagentos.projects.task_store import ProjectTaskStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ProjectTaskStore(tmp_path / "tasks.db")
    await s.init()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_create_and_get_task(store):
    t = await store.create_task(
        project_id="prj-aaa",
        title="Draft outline",
        body="Use 5 sections",
        created_by="user-1",
    )
    assert t["id"].startswith("tsk-")
    assert t["status"] == "open"
    assert t["title"] == "Draft outline"
    assert t["claimed_by"] is None
    assert t["parent_task_id"] is None

    again = await store.get_task(t["id"])
    assert again == t


@pytest.mark.asyncio
async def test_create_subtask(store):
    parent = await store.create_task(project_id="prj-aaa", title="P", created_by="u")
    child = await store.create_task(
        project_id="prj-aaa",
        title="C",
        created_by="u",
        parent_task_id=parent["id"],
    )
    assert child["parent_task_id"] == parent["id"]


@pytest.mark.asyncio
async def test_list_tasks_filter_by_status(store):
    a = await store.create_task(project_id="p", title="A", created_by="u")
    b = await store.create_task(project_id="p", title="B", created_by="u")
    await store.close_task(b["id"], closed_by="u")

    open_tasks = await store.list_tasks(project_id="p", status="open")
    closed_tasks = await store.list_tasks(project_id="p", status="closed")
    assert [t["id"] for t in open_tasks] == [a["id"]]
    assert [t["id"] for t in closed_tasks] == [b["id"]]


@pytest.mark.asyncio
async def test_atomic_claim_only_one_winner(store):
    t = await store.create_task(project_id="p", title="A", created_by="u")
    first = await store.claim_task(t["id"], claimer_id="agent-1")
    second = await store.claim_task(t["id"], claimer_id="agent-2")
    assert first is True
    assert second is False
    again = await store.get_task(t["id"])
    assert again["claimed_by"] == "agent-1"
    assert again["status"] == "claimed"


@pytest.mark.asyncio
async def test_agent_cannot_hold_two_active_claims(store):
    a = await store.create_task(project_id="p", title="A", created_by="u")
    b = await store.create_task(project_id="p", title="B", created_by="u")
    first = await store.claim_task(a["id"], claimer_id="agent-1")
    second = await store.claim_task(b["id"], claimer_id="agent-1")
    assert first is True
    assert second is False
    assert (await store.get_task(b["id"]))["status"] == "open"
    assert await store.held_task("agent-1") == a["id"]


@pytest.mark.asyncio
async def test_can_claim_again_after_release(store):
    a = await store.create_task(project_id="p", title="A", created_by="u")
    b = await store.create_task(project_id="p", title="B", created_by="u")
    await store.claim_task(a["id"], claimer_id="agent-1")
    await store.release_task(a["id"], releaser_id="agent-1")
    assert await store.held_task("agent-1") is None
    assert await store.claim_task(b["id"], claimer_id="agent-1") is True


@pytest.mark.asyncio
async def test_can_claim_again_after_close(store):
    a = await store.create_task(project_id="p", title="A", created_by="u")
    b = await store.create_task(project_id="p", title="B", created_by="u")
    await store.claim_task(a["id"], claimer_id="agent-1")
    await store.close_task(a["id"], closed_by="agent-1", reason="done")
    assert await store.held_task("agent-1") is None
    assert await store.claim_task(b["id"], claimer_id="agent-1") is True


@pytest.mark.asyncio
async def test_release_task(store):
    t = await store.create_task(project_id="p", title="A", created_by="u")
    await store.claim_task(t["id"], claimer_id="agent-1")
    await store.release_task(t["id"], releaser_id="agent-1")
    again = await store.get_task(t["id"])
    assert again["claimed_by"] is None
    assert again["status"] == "open"


@pytest.mark.asyncio
async def test_release_only_by_claimer(store):
    t = await store.create_task(project_id="p", title="A", created_by="u")
    await store.claim_task(t["id"], claimer_id="agent-1")
    ok = await store.release_task(t["id"], releaser_id="agent-2")
    assert ok is False
    again = await store.get_task(t["id"])
    assert again["claimed_by"] == "agent-1"


@pytest.mark.asyncio
async def test_close_task_records_metadata(store):
    t = await store.create_task(project_id="p", title="A", created_by="u")
    await store.close_task(t["id"], closed_by="agent-1", reason="done")
    again = await store.get_task(t["id"])
    assert again["status"] == "closed"
    assert again["closed_by"] == "agent-1"
    assert again["close_reason"] == "done"
    assert again["closed_at"] is not None


@pytest.mark.asyncio
async def test_reopen_task_returns_closed_task_to_open_pool(store):
    t = await store.create_task(project_id="p", title="A", created_by="u")
    await store.claim_task(t["id"], claimer_id="agent-1")
    await store.close_task(t["id"], closed_by="agent-1", reason="oops")
    assert await store.reopen_task(t["id"], reopened_by="jay") is True
    reopened = await store.get_task(t["id"])
    assert reopened["status"] == "open"
    assert reopened["closed_by"] is None
    assert reopened["closed_at"] is None
    assert reopened["close_reason"] is None
    # reopened task must return to the claimable pool, so the old claimer clears
    assert reopened["claimed_by"] is None
    assert reopened["claimed_at"] is None


@pytest.mark.asyncio
async def test_reopen_task_is_noop_when_not_closed(store):
    t = await store.create_task(project_id="p", title="A", created_by="u")
    assert await store.reopen_task(t["id"], reopened_by="jay") is False
    assert (await store.get_task(t["id"]))["status"] == "open"


@pytest.mark.asyncio
async def test_add_relationship_and_list(store):
    a = await store.create_task(project_id="p", title="A", created_by="u")
    b = await store.create_task(project_id="p", title="B", created_by="u")
    rel = await store.add_relationship(
        project_id="p",
        from_task_id=a["id"],
        to_task_id=b["id"],
        kind="blocks",
        created_by="u",
    )
    assert rel["id"].startswith("rel-")
    rels = await store.list_relationships(a["id"])
    assert [r["to_task_id"] for r in rels] == [b["id"]]


@pytest.mark.asyncio
async def test_ready_tasks_excludes_blocked(store):
    a = await store.create_task(project_id="p", title="A", created_by="u")
    b = await store.create_task(project_id="p", title="B", created_by="u")
    # b blocks a
    await store.add_relationship(
        project_id="p",
        from_task_id=a["id"],
        to_task_id=b["id"],
        kind="blocks",
        created_by="u",
    )
    ready = await store.list_ready_tasks(project_id="p")
    assert [t["id"] for t in ready] == [b["id"]]

    await store.close_task(b["id"], closed_by="u")
    ready = await store.list_ready_tasks(project_id="p")
    assert [t["id"] for t in ready] == [a["id"]]


@pytest.mark.asyncio
async def test_ready_tasks_excludes_claimed(store):
    a = await store.create_task(project_id="p", title="A", created_by="u")
    await store.claim_task(a["id"], "agent-1")
    ready = await store.list_ready_tasks(project_id="p")
    assert ready == []


@pytest.mark.asyncio
async def test_threaded_comments(store):
    t = await store.create_task(project_id="p", title="A", created_by="u")
    c1 = await store.add_comment(task_id=t["id"], author_id="u", body="root")
    c2 = await store.add_comment(
        task_id=t["id"], author_id="u2", body="reply", replies_to_comment_id=c1["id"]
    )
    assert c1["id"].startswith("cmt-")
    assert c2["replies_to_comment_id"] == c1["id"]

    comments = await store.list_comments(task_id=t["id"])
    assert [c["id"] for c in comments] == [c1["id"], c2["id"]]


@pytest.mark.asyncio
async def test_closing_blocker_unblocks_ready_view(store):
    a = await store.create_task(project_id="p", title="A", created_by="u")
    b = await store.create_task(project_id="p", title="B", created_by="u")
    c = await store.create_task(project_id="p", title="C", created_by="u")
    # a is blocked by both b and c
    await store.add_relationship(project_id="p", from_task_id=a["id"], to_task_id=b["id"], kind="blocks", created_by="u")
    await store.add_relationship(project_id="p", from_task_id=a["id"], to_task_id=c["id"], kind="blocks", created_by="u")

    ready = await store.list_ready_tasks(project_id="p")
    assert {t["id"] for t in ready} == {b["id"], c["id"]}

    await store.close_task(b["id"], closed_by="u")
    ready = await store.list_ready_tasks(project_id="p")
    assert {t["id"] for t in ready} == {c["id"]}

    await store.close_task(c["id"], closed_by="u")
    ready = await store.list_ready_tasks(project_id="p")
    assert {t["id"] for t in ready} == {a["id"]}
