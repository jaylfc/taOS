import pytest
import pytest_asyncio

from tinyagentos.projects.events import ProjectEventBroker
from tinyagentos.projects.task_store import ProjectTaskStore


@pytest_asyncio.fixture
async def store(tmp_path):
    s = ProjectTaskStore(tmp_path / "tasks.db")
    await s.init()
    yield s
    await s.close()


@pytest_asyncio.fixture
async def store_with_broker(tmp_path):
    broker = ProjectEventBroker()
    s = ProjectTaskStore(tmp_path / "tasks.db", broker=broker)
    await s.init()
    yield s, broker
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
async def test_unquarantine_returns_claimed_task_to_claimable_pool(store):
    t = await store.create_task(project_id="p", title="A", created_by="u")
    await store.claim_task(t["id"], claimer_id="agent-1")
    assert await store.quarantine_task(t["id"], actor="lead") is True
    assert await store.unquarantine_task(t["id"], actor="lead") is True
    back = await store.get_task(t["id"])
    assert back["status"] == "open"
    # unquarantined task must return to the claimable pool, so the old claimer clears
    assert back["claimed_by"] is None
    assert back["claimed_at"] is None
    # the pool must be real: claim_task requires claimed_by IS NULL, so a stale
    # claimer would leave the card open-but-unclaimable forever
    assert await store.claim_task(t["id"], claimer_id="agent-2") is True


@pytest.mark.asyncio
async def test_update_task_status_open_returns_task_to_claimable_pool(store):
    # Same class via the generic edit path: the owner/admin PATCH route can
    # set status='open' on a claimed card directly through update_task.
    t = await store.create_task(project_id="p", title="A", created_by="u")
    await store.claim_task(t["id"], claimer_id="agent-1")
    await store.update_task(t["id"], status="open")
    back = await store.get_task(t["id"])
    assert back["status"] == "open"
    assert back["claimed_by"] is None
    assert back["claimed_at"] is None
    assert await store.claim_task(t["id"], claimer_id="agent-2") is True


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


# ---------------------------------------------------------------------------
# get_task_context — goal ancestry + blocker graph
# ---------------------------------------------------------------------------

class _FakeProjectStore:
    def __init__(self, projects: dict):
        self._projects = projects

    async def get_project(self, project_id: str):
        return self._projects.get(project_id)


@pytest.mark.asyncio
async def test_get_task_context_not_found(store):
    with pytest.raises(ValueError):
        await store.get_task_context("tsk-nope")


@pytest.mark.asyncio
async def test_get_task_context_ancestry_order(store):
    root = await store.create_task(project_id="p", title="Root", created_by="u")
    mid = await store.create_task(
        project_id="p", title="Mid", created_by="u", parent_task_id=root["id"]
    )
    leaf = await store.create_task(
        project_id="p", title="Leaf", created_by="u", parent_task_id=mid["id"]
    )

    ctx = await store.get_task_context(leaf["id"])
    assert [a["id"] for a in ctx["ancestry"]] == [root["id"], mid["id"]]
    assert ctx["ancestry"][0]["title"] == "Root"
    assert ctx["ancestry"][1]["title"] == "Mid"
    # The task itself is excluded from its own ancestry.
    assert leaf["id"] not in [a["id"] for a in ctx["ancestry"]]


@pytest.mark.asyncio
async def test_get_task_context_no_ancestry_for_root_task(store):
    root = await store.create_task(project_id="p", title="Root", created_by="u")
    ctx = await store.get_task_context(root["id"])
    assert ctx["ancestry"] == []


@pytest.mark.asyncio
async def test_get_task_context_cycle_guard(store):
    a = await store.create_task(project_id="p", title="A", created_by="u")
    b = await store.create_task(
        project_id="p", title="B", created_by="u", parent_task_id=a["id"]
    )
    # Force a cycle: a's parent becomes b, so a -> b -> a.
    await store.update_task(a["id"], parent_task_id=b["id"])

    ctx = await store.get_task_context(a["id"])
    # Must terminate (no infinite loop / crash) and not include a itself.
    assert a["id"] not in [x["id"] for x in ctx["ancestry"]]
    assert len(ctx["ancestry"]) <= 2


@pytest.mark.asyncio
async def test_get_task_context_blockers_and_is_blocked(store):
    dependent = await store.create_task(project_id="p", title="Dependent", created_by="u")
    blocker = await store.create_task(project_id="p", title="Blocker", created_by="u")
    await store.add_relationship(
        project_id="p", from_task_id=dependent["id"], to_task_id=blocker["id"],
        kind="blocks", created_by="u",
    )

    ctx = await store.get_task_context(dependent["id"])
    assert [b["id"] for b in ctx["blockers"]] == [blocker["id"]]
    assert ctx["is_blocked"] is True

    await store.close_task(blocker["id"], closed_by="u")
    ctx = await store.get_task_context(dependent["id"])
    assert ctx["is_blocked"] is False
    assert ctx["blockers"][0]["status"] == "closed"


@pytest.mark.asyncio
async def test_get_task_context_ignores_non_blocks_relationships(store):
    a = await store.create_task(project_id="p", title="A", created_by="u")
    b = await store.create_task(project_id="p", title="B", created_by="u")
    await store.add_relationship(
        project_id="p", from_task_id=a["id"], to_task_id=b["id"],
        kind="relates_to", created_by="u",
    )
    ctx = await store.get_task_context(a["id"])
    assert ctx["blockers"] == []
    assert ctx["is_blocked"] is False


@pytest.mark.asyncio
async def test_get_task_context_project_enrichment(tmp_path):
    fake_project_store = _FakeProjectStore(
        {"prj-1": {"id": "prj-1", "name": "Alpha", "description": "Ship the thing"}}
    )
    s = ProjectTaskStore(tmp_path / "tasks2.db", project_store=fake_project_store)
    await s.init()
    try:
        t = await s.create_task(project_id="prj-1", title="T", created_by="u")
        ctx = await s.get_task_context(t["id"])
        assert ctx["project"] == {"id": "prj-1", "name": "Alpha", "description": "Ship the thing"}
    finally:
        await s.close()


@pytest.mark.asyncio
async def test_get_task_context_project_falls_back_without_project_store(store):
    t = await store.create_task(project_id="p", title="T", created_by="u")
    ctx = await store.get_task_context(t["id"])
    assert ctx["project"]["id"] == "p"


# ---------------------------------------------------------------------------
# Checklist item tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_checklist_item(store):
    t = await store.create_task(project_id="p", title="Objective", created_by="u")
    item = await store.create_checklist_item(task_id=t["id"], text="First item", created_by="u")
    assert item["id"].startswith("cki-")
    assert item["text"] == "First item"
    assert item["done"] is False
    assert item["verified"] is False
    assert item["reported"] is False
    assert item["archived"] is False

    items = await store.list_checklist_items(task_id=t["id"])
    assert len(items) == 1
    assert items[0]["id"] == item["id"]

    all_items = await store.list_checklist_items(task_id=t["id"], include_archived=True)
    assert len(all_items) == 1


@pytest.mark.asyncio
async def test_cannot_archive_unverified(store):
    t = await store.create_task(project_id="p", title="Objective", created_by="u")
    item = await store.create_checklist_item(task_id=t["id"], text="Unverified item", created_by="u")
    with pytest.raises(ValueError, match="item cannot be archived: not verified"):
        await store.archive_checklist_item(item_id=item["id"], reported_by="u")


@pytest.mark.asyncio
async def test_cannot_archive_unreported(store):
    t = await store.create_task(project_id="p", title="Objective", created_by="u")
    item = await store.create_checklist_item(task_id=t["id"], text="Unreported item", created_by="u")
    await store.update_checklist_item(item_id=item["id"], verified=True)
    with pytest.raises(ValueError, match="item cannot be archived: not reported"):
        await store.archive_checklist_item(item_id=item["id"], reported_by="u")


@pytest.mark.asyncio
async def test_can_archive_after_verification_and_report(store):
    t = await store.create_task(project_id="p", title="Objective", created_by="u")
    item = await store.create_checklist_item(task_id=t["id"], text="Complete item", created_by="u")
    await store.update_checklist_item(item_id=item["id"], verified=True, reported=True)
    archived = await store.archive_checklist_item(item_id=item["id"], reported_by="u")
    assert archived["archived"] is True
    all_items = await store.list_checklist_items(task_id=t["id"], include_archived=True)
    assert any(i["id"] == item["id"] for i in all_items)


@pytest.mark.asyncio
async def test_survives_agent_restart(store):
    t = await store.create_task(project_id="p", title="Objective", created_by="u")
    item = await store.create_checklist_item(task_id=t["id"], text="Persistent item", created_by="u")
    items = await store.list_checklist_items(task_id=t["id"])
    assert len(items) == 1
    assert items[0]["text"] == "Persistent item"
    assert items[0]["archived"] is False
    all_items = await store.list_checklist_items(task_id=t["id"], include_archived=True)
    assert len(all_items) == 1


@pytest.mark.asyncio
async def test_checklist_item_event_delivered_at_project_scope(store_with_broker):
    """Defect 1: checklist.item.created must be published under the PROJECT id
    so project-scoped subscribers receive it.

    On the BASE branch the event is published under the task_id, so the project
    subscription never fires and this test fails.
    """
    store, broker = store_with_broker
    t = await store.create_task(project_id="proj-red", title="Objective", created_by="u")
    queue = await broker.subscribe("proj-red")
    await store.create_checklist_item(task_id=t["id"], text="step one", created_by="u")
    collected = []
    while not queue.empty():
        collected.append(queue.get_nowait())
    checklist_events = [e for e in collected if e.kind == "checklist.item.created"]
    assert checklist_events, (
        f"expected checklist.item.created at project scope, got: {[e.kind for e in collected]}"
    )
    assert checklist_events[0].payload["task_id"] == t["id"]


@pytest.mark.asyncio
async def test_archive_nonexistent_item_raises_value_error(store):
    """Defect 2: archiving a missing checklist item should raise a clean
    ValueError, not a TypeError from indexing None.
    """
    with pytest.raises(ValueError, match="not found"):
        await store.archive_checklist_item(item_id="cki-nonexistent", reported_by="u")


# ── close_task ownership guard ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_close_by_claimer_passes(store):
    """Claim holder can close their own claimed card."""
    t = await store.create_task(project_id="p", title="A", created_by="u")
    await store.claim_task(t["id"], claimer_id="agent-1")
    ok = await store.close_task(t["id"], closed_by="agent-1", reason="done")
    assert ok is True
    again = await store.get_task(t["id"])
    assert again["status"] == "closed"
    assert again["closed_by"] == "agent-1"


@pytest.mark.asyncio
async def test_close_by_stranger_rejected(store):
    """A non-claimer cannot close a claimed card (ownership guard)."""
    t = await store.create_task(project_id="p", title="A", created_by="u")
    await store.claim_task(t["id"], claimer_id="agent-1")
    ok = await store.close_task(t["id"], closed_by="agent-2", reason="intruder")
    assert ok is False
    again = await store.get_task(t["id"])
    assert again["status"] == "claimed"
    assert again["claimed_by"] == "agent-1"


@pytest.mark.asyncio
async def test_close_by_lead_passes(store):
    """Lead/curator can force-close a card claimed by someone else."""
    t = await store.create_task(project_id="p", title="A", created_by="u")
    await store.claim_task(t["id"], claimer_id="agent-1")
    ok = await store.close_task(t["id"], closed_by="lead", reason="escalation", force=True)
    assert ok is True
    again = await store.get_task(t["id"])
    assert again["status"] == "closed"
    assert again["closed_by"] == "lead"


@pytest.mark.asyncio
async def test_close_unclaimed_unchanged(store):
    """Unclaimed cards can still be closed by any authorised caller."""
    t = await store.create_task(project_id="p", title="A", created_by="u")
    ok = await store.close_task(t["id"], closed_by="reviewer", reason="stale")
    assert ok is True
    again = await store.get_task(t["id"])
    assert again["status"] == "closed"
    assert again["closed_by"] == "reviewer"
