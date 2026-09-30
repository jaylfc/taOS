import pytest
import pytest_asyncio

from tinyagentos.board_audit import BoardAuditLog
from tinyagentos.projects.task_store import ProjectTaskStore


@pytest_asyncio.fixture
async def store_with_audit(tmp_path):
    audit = BoardAuditLog(tmp_path / "audit.db")
    await audit.init()
    s = ProjectTaskStore(tmp_path / "tasks.db", audit=audit)
    await s.init()
    yield s, audit
    await s.close()
    await audit.close()


@pytest.mark.asyncio
async def test_assign_if_unassigned_sets_assignee_and_audits(store_with_audit):
    store, audit = store_with_audit
    t = await store.create_task(project_id="prj-1", title="A", created_by="u")

    result = await store.assign_if_unassigned(t["id"], "agent-1", "dispatcher")

    assert result is True
    task = await store.get_task(t["id"])
    assert task["assignee_id"] == "agent-1"
    assert task["status"] == "open"
    assert task["claimed_by"] is None
    history = await audit.history(t["id"])
    assigned_events = [h for h in history if h["event"] == "task.assigned"]
    assert len(assigned_events) == 1
    assert assigned_events[0]["actor"] == "dispatcher"
    assert assigned_events[0]["from_status"] == "open"
    assert assigned_events[0]["to_status"] == "open"
    assert assigned_events[0]["project_id"] == "prj-1"


@pytest.mark.asyncio
async def test_assign_if_unassigned_refuses_claimed_card(store_with_audit):
    store, _ = store_with_audit
    t = await store.create_task(project_id="prj-1", title="A", created_by="u")
    await store.claim_task(t["id"], "agent-1")

    result = await store.assign_if_unassigned(t["id"], "agent-2", "dispatcher")

    assert result is False
    task = await store.get_task(t["id"])
    assert task["assignee_id"] is None
    assert task["claimed_by"] == "agent-1"


@pytest.mark.asyncio
async def test_assign_if_unassigned_refuses_specific_assignee(store_with_audit):
    store, _ = store_with_audit
    t = await store.create_task(project_id="prj-1", title="A", created_by="u", assignee_id="agent-x")

    result = await store.assign_if_unassigned(t["id"], "agent-1", "dispatcher")

    assert result is False
    task = await store.get_task(t["id"])
    assert task["assignee_id"] == "agent-x"


@pytest.mark.asyncio
async def test_assign_if_unassigned_accepts_at_any_pool(store_with_audit):
    store, audit = store_with_audit
    t = await store.create_task(project_id="prj-1", title="A", created_by="u", assignee_id="@any")

    result = await store.assign_if_unassigned(t["id"], "agent-1", "dispatcher")

    assert result is True
    task = await store.get_task(t["id"])
    assert task["assignee_id"] == "agent-1"
    history = await audit.history(t["id"])
    assigned_events = [h for h in history if h["event"] == "task.assigned"]
    assert len(assigned_events) == 1


@pytest.mark.asyncio
async def test_unassign_if_refuses_when_assignee_changed(store_with_audit):
    store, _ = store_with_audit
    t = await store.create_task(project_id="prj-1", title="A", created_by="u", assignee_id="agent-x")

    result = await store.unassign_if(t["id"], "agent-1", "dispatcher")

    assert result is False
    task = await store.get_task(t["id"])
    assert task["assignee_id"] == "agent-x"


@pytest.mark.asyncio
async def test_unassign_if_refuses_claimed_card(store_with_audit):
    store, _ = store_with_audit
    t = await store.create_task(project_id="prj-1", title="A", created_by="u", assignee_id="agent-1")
    await store.claim_task(t["id"], "agent-1")

    result = await store.unassign_if(t["id"], "agent-1", "dispatcher")

    assert result is False
    task = await store.get_task(t["id"])
    assert task["assignee_id"] == "agent-1"
    assert task["claimed_by"] == "agent-1"


@pytest.mark.asyncio
async def test_count_open_load_counts_claimed_and_assigned_unclaimed(store_with_audit):
    store, _ = store_with_audit
    a_claimed = await store.create_task(project_id="prj-1", title="A", created_by="u", assignee_id="agent-a")
    await store.claim_task(a_claimed["id"], "agent-a")
    b_assigned = await store.create_task(project_id="prj-2", title="B", created_by="u", assignee_id="agent-b")
    closed_task = await store.create_task(project_id="prj-1", title="Closed", created_by="u", assignee_id="agent-a")
    await store.close_task(closed_task["id"], closed_by="u")

    count = await store.count_open_load(["agent-a", "agent-b"])

    assert count == 2


@pytest.mark.asyncio
async def test_count_open_load_empty_list_returns_zero(store_with_audit):
    store, _ = store_with_audit

    count = await store.count_open_load([])

    assert count == 0


@pytest.mark.asyncio
async def test_count_open_load_by_project_groups_per_board(store_with_audit):
    store, _ = store_with_audit
    a_claimed = await store.create_task(project_id="prj-1", title="A", created_by="u", assignee_id="agent-a")
    await store.claim_task(a_claimed["id"], "agent-a")
    b_assigned = await store.create_task(project_id="prj-1", title="B", created_by="u", assignee_id="agent-b")
    c_other = await store.create_task(project_id="prj-2", title="C", created_by="u", assignee_id="agent-a")
    await store.claim_task(c_other["id"], "agent-a")

    counts = await store.count_open_load_by_project(["agent-a", "agent-b"])

    assert counts == {"prj-1": 2, "prj-2": 1}
