import json
from unittest.mock import AsyncMock, patch

import pytest

from tinyagentos.projects.task_store import ProjectTaskStore


async def _store(tmp_path):
    s = ProjectTaskStore(tmp_path / "tasks.db")
    await s.init()
    return s


@pytest.mark.asyncio
async def test_create_task_defaults(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Fix bug", "alice")
    assert task["id"].startswith("tsk-")
    assert task["project_id"] == "prj-1"
    assert task["title"] == "Fix bug"
    assert task["body"] == ""
    assert task["status"] == "open"
    assert task["priority"] == 0
    assert task["labels"] == []
    assert task["assignee_id"] is None
    assert task["created_by"] == "alice"
    assert task["claimed_by"] is None
    assert task["closed_by"] is None
    assert task["close_reason"] is None
    assert task["parent_task_id"] is None
    assert isinstance(task["created_at"], float)
    assert isinstance(task["updated_at"], float)
    await s.close()


@pytest.mark.asyncio
async def test_create_task_with_all_fields(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task(
        "prj-1",
        "Write docs",
        "bob",
        body="detailed description",
        priority=2,
        labels=["docs", "urgent"],
        assignee_id="carol",
        parent_task_id="tsk-parent",
    )
    assert task["title"] == "Write docs"
    assert task["body"] == "detailed description"
    assert task["priority"] == 2
    assert task["labels"] == ["docs", "urgent"]
    assert task["assignee_id"] == "carol"
    assert task["parent_task_id"] == "tsk-parent"
    await s.close()


@pytest.mark.asyncio
async def test_create_task_labels_stored_as_json(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "x", "alice", labels=["a", "b"])
    fetched = await s.get_task(task["id"])
    assert fetched["labels"] == ["a", "b"]
    await s.close()


@pytest.mark.asyncio
async def test_get_task_not_found(tmp_path):
    s = await _store(tmp_path)
    result = await s.get_task("tsk-nonexistent")
    assert result is None
    await s.close()


@pytest.mark.asyncio
async def test_list_tasks_empty_project(tmp_path):
    s = await _store(tmp_path)
    tasks = await s.list_tasks("prj-empty")
    assert tasks == []
    await s.close()


@pytest.mark.asyncio
async def test_list_tasks_returns_all_in_project(tmp_path):
    s = await _store(tmp_path)
    t1 = await s.create_task("prj-1", "First", "alice")
    t2 = await s.create_task("prj-1", "Second", "bob")
    await s.create_task("prj-2", "Other", "eve")
    tasks = await s.list_tasks("prj-1")
    assert len(tasks) == 2
    ids = [t["id"] for t in tasks]
    assert t1["id"] in ids
    assert t2["id"] in ids
    await s.close()


@pytest.mark.asyncio
async def test_list_tasks_filter_by_status(tmp_path):
    s = await _store(tmp_path)
    t_open = await s.create_task("prj-1", "Open task", "alice")
    t_claimed = await s.create_task("prj-1", "Claimed task", "alice")
    await s.claim_task(t_claimed["id"], "worker-1")
    open_tasks = await s.list_tasks("prj-1", status="open")
    assert len(open_tasks) == 1
    assert open_tasks[0]["id"] == t_open["id"]
    claimed_tasks = await s.list_tasks("prj-1", status="claimed")
    assert len(claimed_tasks) == 1
    assert claimed_tasks[0]["id"] == t_claimed["id"]
    await s.close()


@pytest.mark.asyncio
async def test_list_tasks_filter_by_parent(tmp_path):
    s = await _store(tmp_path)
    parent = await s.create_task("prj-1", "Parent", "alice")
    child = await s.create_task("prj-1", "Child", "alice", parent_task_id=parent["id"])
    await s.create_task("prj-1", "Orphan", "alice")
    children = await s.list_tasks("prj-1", parent_task_id=parent["id"])
    assert len(children) == 1
    assert children[0]["id"] == child["id"]
    await s.close()


@pytest.mark.asyncio
async def test_claim_task_success(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Do work", "alice")
    ok = await s.claim_task(task["id"], "worker-1")
    assert ok is True
    fetched = await s.get_task(task["id"])
    assert fetched["status"] == "claimed"
    assert fetched["claimed_by"] == "worker-1"
    assert isinstance(fetched["claimed_at"], float)
    await s.close()


@pytest.mark.asyncio
async def test_claim_task_double_claim_rejected(tmp_path):
    s = await _store(tmp_path)
    t1 = await s.create_task("prj-1", "Task A", "alice")
    t2 = await s.create_task("prj-1", "Task B", "alice")
    ok1 = await s.claim_task(t1["id"], "worker-1")
    assert ok1 is True
    ok2 = await s.claim_task(t2["id"], "worker-1")
    assert ok2 is False
    await s.close()


@pytest.mark.asyncio
async def test_claim_already_claimed_by_other_rejected(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    await s.claim_task(task["id"], "worker-1")
    ok = await s.claim_task(task["id"], "worker-2")
    assert ok is False
    fetched = await s.get_task(task["id"])
    assert fetched["claimed_by"] == "worker-1"
    await s.close()


@pytest.mark.asyncio
async def test_claim_closed_task_rejected(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    await s.claim_task(task["id"], "worker-1")
    await s.close_task(task["id"], "worker-1")
    ok = await s.claim_task(task["id"], "worker-2")
    assert ok is False
    await s.close()


@pytest.mark.asyncio
async def test_release_task_success(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    await s.claim_task(task["id"], "worker-1")
    ok = await s.release_task(task["id"], "worker-1")
    assert ok is True
    fetched = await s.get_task(task["id"])
    assert fetched["status"] == "open"
    assert fetched["claimed_by"] is None
    assert fetched["claimed_at"] is None
    await s.close()


@pytest.mark.asyncio
async def test_release_task_wrong_releaser_rejected(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    await s.claim_task(task["id"], "worker-1")
    ok = await s.release_task(task["id"], "worker-2")
    assert ok is False
    fetched = await s.get_task(task["id"])
    assert fetched["status"] == "claimed"
    await s.close()


@pytest.mark.asyncio
async def test_release_open_task_noop(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    ok = await s.release_task(task["id"], "worker-1")
    assert ok is False
    await s.close()


@pytest.mark.asyncio
async def test_close_task_from_claimed(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    await s.claim_task(task["id"], "worker-1")
    ok = await s.close_task(task["id"], "worker-1", reason="done")
    assert ok is True
    fetched = await s.get_task(task["id"])
    assert fetched["status"] == "closed"
    assert fetched["closed_by"] == "worker-1"
    assert fetched["close_reason"] == "done"
    assert isinstance(fetched["closed_at"], float)
    await s.close()


@pytest.mark.asyncio
async def test_close_task_from_open(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    ok = await s.close_task(task["id"], "alice")
    assert ok is True
    fetched = await s.get_task(task["id"])
    assert fetched["status"] == "closed"
    assert fetched["closed_by"] == "alice"
    await s.close()


@pytest.mark.asyncio
async def test_close_already_closed_task_rejected(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    await s.close_task(task["id"], "alice")
    ok = await s.close_task(task["id"], "alice")
    assert ok is False
    await s.close()


@pytest.mark.asyncio
async def test_close_cancelled_task_rejected(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    await s.close_task(task["id"], "alice")
    await s.reopen_task(task["id"], "alice")
    # manually set to cancelled
    await s._db.execute("UPDATE project_tasks SET status = 'cancelled' WHERE id = ?", (task["id"],))
    await s._db.commit()
    ok = await s.close_task(task["id"], "alice")
    assert ok is False
    await s.close()


@pytest.mark.asyncio
async def test_reopen_task_success(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    await s.claim_task(task["id"], "worker-1")
    await s.close_task(task["id"], "worker-1")
    ok = await s.reopen_task(task["id"], "alice")
    assert ok is True
    fetched = await s.get_task(task["id"])
    assert fetched["status"] == "open"
    assert fetched["closed_by"] is None
    assert fetched["closed_at"] is None
    assert fetched["close_reason"] is None
    assert fetched["claimed_by"] is None
    assert fetched["claimed_at"] is None
    await s.close()


@pytest.mark.asyncio
async def test_reopen_open_task_noop(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    ok = await s.reopen_task(task["id"], "alice")
    assert ok is False
    await s.close()


@pytest.mark.asyncio
async def test_held_task_returns_active_claim(tmp_path):
    s = await _store(tmp_path)
    t1 = await s.create_task("prj-1", "Task A", "alice")
    t2 = await s.create_task("prj-1", "Task B", "alice")
    await s.claim_task(t1["id"], "worker-1")
    await s.claim_task(t2["id"], "worker-2")
    held = await s.held_task("worker-1")
    assert held == t1["id"]
    held2 = await s.held_task("worker-2")
    assert held2 == t2["id"]
    await s.close()


@pytest.mark.asyncio
async def test_held_task_none_when_no_claim(tmp_path):
    s = await _store(tmp_path)
    result = await s.held_task("worker-1")
    assert result is None
    await s.close()


@pytest.mark.asyncio
async def test_held_task_none_after_close(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    await s.claim_task(task["id"], "worker-1")
    await s.close_task(task["id"], "worker-1")
    held = await s.held_task("worker-1")
    assert held is None
    await s.close()


@pytest.mark.asyncio
async def test_update_task_title(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Old title", "alice")
    await s.update_task(task["id"], title="New title")
    fetched = await s.get_task(task["id"])
    assert fetched["title"] == "New title"
    await s.close()


@pytest.mark.asyncio
async def test_update_task_labels(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    await s.update_task(task["id"], labels=["bug", "critical"])
    fetched = await s.get_task(task["id"])
    assert fetched["labels"] == ["bug", "critical"]
    await s.close()


@pytest.mark.asyncio
async def test_update_task_multiple_fields(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    await s.update_task(task["id"], body="new body", priority=5, assignee_id="bob")
    fetched = await s.get_task(task["id"])
    assert fetched["body"] == "new body"
    assert fetched["priority"] == 5
    assert fetched["assignee_id"] == "bob"
    await s.close()


@pytest.mark.asyncio
async def test_update_task_no_op_when_all_none(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    before = await s.get_task(task["id"])
    await s.update_task(task["id"])
    after = await s.get_task(task["id"])
    assert before["updated_at"] == after["updated_at"]
    await s.close()


@pytest.mark.asyncio
async def test_add_and_list_relationship(tmp_path):
    s = await _store(tmp_path)
    t1 = await s.create_task("prj-1", "Blocks", "alice")
    t2 = await s.create_task("prj-1", "Blocked", "alice")
    rel = await s.add_relationship("prj-1", t1["id"], t2["id"], "blocks", "alice")
    assert rel["from_task_id"] == t1["id"]
    assert rel["to_task_id"] == t2["id"]
    assert rel["kind"] == "blocks"
    rels = await s.list_relationships(t1["id"], direction="from")
    assert len(rels) == 1
    assert rels[0]["id"] == rel["id"]
    await s.close()


@pytest.mark.asyncio
async def test_add_relationship_invalid_kind(tmp_path):
    s = await _store(tmp_path)
    t1 = await s.create_task("prj-1", "A", "alice")
    t2 = await s.create_task("prj-1", "B", "alice")
    with pytest.raises(ValueError, match="invalid relationship kind"):
        await s.add_relationship("prj-1", t1["id"], t2["id"], "invalid_kind", "alice")
    await s.close()


@pytest.mark.asyncio
async def test_add_relationship_task_not_in_project(tmp_path):
    s = await _store(tmp_path)
    t1 = await s.create_task("prj-1", "A", "alice")
    t2 = await s.create_task("prj-2", "B", "alice")
    with pytest.raises(ValueError, match="task not in project"):
        await s.add_relationship("prj-1", t1["id"], t2["id"], "blocks", "alice")
    await s.close()


@pytest.mark.asyncio
async def test_remove_relationship(tmp_path):
    s = await _store(tmp_path)
    t1 = await s.create_task("prj-1", "A", "alice")
    t2 = await s.create_task("prj-1", "B", "alice")
    rel = await s.add_relationship("prj-1", t1["id"], t2["id"], "blocks", "alice")
    await s.remove_relationship(rel["id"])
    rels = await s.list_relationships(t1["id"])
    assert rels == []
    await s.close()


@pytest.mark.asyncio
async def test_list_relationships_direction_to(tmp_path):
    s = await _store(tmp_path)
    t1 = await s.create_task("prj-1", "A", "alice")
    t2 = await s.create_task("prj-1", "B", "alice")
    rel = await s.add_relationship("prj-1", t1["id"], t2["id"], "blocks", "alice")
    rels = await s.list_relationships(t2["id"], direction="to")
    assert len(rels) == 1
    assert rels[0]["id"] == rel["id"]
    await s.close()


@pytest.mark.asyncio
async def test_list_relationships_invalid_direction(tmp_path):
    s = await _store(tmp_path)
    with pytest.raises(ValueError, match="invalid direction"):
        await s.list_relationships("tsk-1", direction="sideways")
    await s.close()


@pytest.mark.asyncio
async def test_list_ready_tasks_excludes_blocked(tmp_path):
    s = await _store(tmp_path)
    ready = await s.create_task("prj-1", "Ready", "alice")
    blocked = await s.create_task("prj-1", "Blocked", "alice")
    blocker = await s.create_task("prj-1", "Blocker", "alice")
    await s.add_relationship("prj-1", blocked["id"], blocker["id"], "blocks", "alice")
    result = await s.list_ready_tasks("prj-1")
    ids = [t["id"] for t in result]
    assert ready["id"] in ids
    assert blocked["id"] not in ids
    assert blocker["id"] in ids
    await s.close()


@pytest.mark.asyncio
async def test_list_ready_tasks_excludes_claimed(tmp_path):
    s = await _store(tmp_path)
    free = await s.create_task("prj-1", "Free", "alice")
    claimed = await s.create_task("prj-1", "Claimed", "alice")
    await s.claim_task(claimed["id"], "worker-1")
    result = await s.list_ready_tasks("prj-1")
    ids = [t["id"] for t in result]
    assert free["id"] in ids
    assert claimed["id"] not in ids
    await s.close()


@pytest.mark.asyncio
async def test_list_ready_tasks_ordered_by_priority_then_created(tmp_path):
    s = await _store(tmp_path)
    low = await s.create_task("prj-1", "Low", "alice", priority=1)
    high = await s.create_task("prj-1", "High", "alice", priority=10)
    mid = await s.create_task("prj-1", "Mid", "alice", priority=5)
    result = await s.list_ready_tasks("prj-1")
    ids = [t["id"] for t in result]
    assert ids == [high["id"], mid["id"], low["id"]]
    await s.close()


@pytest.mark.asyncio
async def test_list_ready_tasks_limit_clamped(tmp_path):
    s = await _store(tmp_path)
    for i in range(5):
        await s.create_task("prj-1", f"Task {i}", "alice")
    result = await s.list_ready_tasks("prj-1", limit=3)
    assert len(result) == 3
    await s.close()


@pytest.mark.asyncio
async def test_list_ready_tasks_for_assignee_matches_only_that_assignee(tmp_path):
    s = await _store(tmp_path)
    mine = await s.create_task("prj-1", "Mine", "alice", assignee_id="agent-1")
    theirs = await s.create_task("prj-1", "Theirs", "alice", assignee_id="agent-2")
    unassigned = await s.create_task("prj-1", "Unassigned", "alice")
    result = await s.list_ready_tasks_for_assignee("agent-1")
    ids = [t["id"] for t in result]
    assert ids == [mine["id"]]
    assert theirs["id"] not in ids
    assert unassigned["id"] not in ids
    await s.close()


@pytest.mark.asyncio
async def test_list_ready_tasks_for_assignee_excludes_claimed(tmp_path):
    s = await _store(tmp_path)
    free = await s.create_task("prj-1", "Free", "alice", assignee_id="agent-1")
    claimed = await s.create_task("prj-1", "Claimed", "alice", assignee_id="agent-1")
    await s.claim_task(claimed["id"], "agent-1")
    result = await s.list_ready_tasks_for_assignee("agent-1")
    ids = [t["id"] for t in result]
    assert free["id"] in ids
    assert claimed["id"] not in ids
    await s.close()


@pytest.mark.asyncio
async def test_list_ready_tasks_for_assignee_excludes_blocked(tmp_path):
    s = await _store(tmp_path)
    ready = await s.create_task("prj-1", "Ready", "alice", assignee_id="agent-1")
    blocked = await s.create_task("prj-1", "Blocked", "alice", assignee_id="agent-1")
    blocker = await s.create_task("prj-1", "Blocker", "alice", assignee_id="agent-1")
    await s.add_relationship("prj-1", blocked["id"], blocker["id"], "blocks", "alice")
    result = await s.list_ready_tasks_for_assignee("agent-1")
    ids = [t["id"] for t in result]
    assert ready["id"] in ids
    assert blocked["id"] not in ids
    assert blocker["id"] in ids
    await s.close()


@pytest.mark.asyncio
async def test_list_ready_tasks_for_assignee_ordered_by_priority_then_created(tmp_path):
    s = await _store(tmp_path)
    low = await s.create_task("prj-1", "Low", "alice", priority=1, assignee_id="agent-1")
    high = await s.create_task("prj-1", "High", "alice", priority=10, assignee_id="agent-1")
    mid = await s.create_task("prj-1", "Mid", "alice", priority=5, assignee_id="agent-1")
    result = await s.list_ready_tasks_for_assignee("agent-1")
    ids = [t["id"] for t in result]
    assert ids == [high["id"], mid["id"], low["id"]]
    await s.close()


@pytest.mark.asyncio
async def test_add_and_list_comments(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    c1 = await s.add_comment(task["id"], "alice", "first comment")
    c2 = await s.add_comment(task["id"], "bob", "second comment")
    comments = await s.list_comments(task["id"])
    assert len(comments) == 2
    assert comments[0]["body"] == "first comment"
    assert comments[1]["body"] == "second comment"
    assert comments[0]["author_id"] == "alice"
    await s.close()


@pytest.mark.asyncio
async def test_add_comment_reply(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    parent = await s.add_comment(task["id"], "alice", "parent")
    child = await s.add_comment(task["id"], "bob", "reply", replies_to_comment_id=parent["id"])
    assert child["replies_to_comment_id"] == parent["id"]
    await s.close()


@pytest.mark.asyncio
async def test_add_comment_reply_wrong_task_rejected(tmp_path):
    s = await _store(tmp_path)
    t1 = await s.create_task("prj-1", "Task 1", "alice")
    t2 = await s.create_task("prj-1", "Task 2", "alice")
    c = await s.add_comment(t1["id"], "alice", "comment")
    with pytest.raises(ValueError, match="replies_to_comment_id not in this task"):
        await s.add_comment(t2["id"], "bob", "reply", replies_to_comment_id=c["id"])
    await s.close()


@pytest.mark.asyncio
async def test_add_comment_reply_nonexistent_rejected(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    with pytest.raises(ValueError, match="replies_to_comment_id not in this task"):
        await s.add_comment(task["id"], "bob", "reply", replies_to_comment_id="cmt-fake")
    await s.close()


@pytest.mark.asyncio
async def test_create_task_publishes_event(tmp_path):
    mock_broker = AsyncMock()
    s = ProjectTaskStore(tmp_path / "tasks.db", broker=mock_broker)
    await s.init()
    task = await s.create_task("prj-1", "Task", "alice")
    mock_broker.publish.assert_called_once()
    args = mock_broker.publish.call_args
    assert args[0][0] == "prj-1"
    assert args[0][1].kind == "task.created"
    assert args[0][1].payload["id"] == task["id"]
    await s.close()


@pytest.mark.asyncio
async def test_claim_task_publishes_event(tmp_path):
    mock_broker = AsyncMock()
    s = ProjectTaskStore(tmp_path / "tasks.db", broker=mock_broker)
    await s.init()
    task = await s.create_task("prj-1", "Task", "alice")
    mock_broker.reset_mock()
    await s.claim_task(task["id"], "worker-1")
    mock_broker.publish.assert_called_once()
    args = mock_broker.publish.call_args
    assert args[0][1].kind == "task.claimed"
    assert args[0][1].payload["claimed_by"] == "worker-1"
    await s.close()


@pytest.mark.asyncio
async def test_close_task_publishes_event(tmp_path):
    mock_broker = AsyncMock()
    s = ProjectTaskStore(tmp_path / "tasks.db", broker=mock_broker)
    await s.init()
    task = await s.create_task("prj-1", "Task", "alice")
    await s.claim_task(task["id"], "worker-1")
    mock_broker.reset_mock()
    await s.close_task(task["id"], "worker-1")
    mock_broker.publish.assert_called_once()
    args = mock_broker.publish.call_args
    assert args[0][1].kind == "task.closed"
    await s.close()


@pytest.mark.asyncio
async def test_no_broker_no_error(tmp_path):
    s = await _store(tmp_path)
    task = await s.create_task("prj-1", "Task", "alice")
    await s.claim_task(task["id"], "worker-1")
    await s.close_task(task["id"], "worker-1")
    await s.close()


@pytest.mark.asyncio
async def test_list_tasks_ordered_by_created_at_asc(tmp_path):
    s = await _store(tmp_path)
    t1 = await s.create_task("prj-1", "First", "alice")
    t2 = await s.create_task("prj-1", "Second", "alice")
    t3 = await s.create_task("prj-1", "Third", "alice")
    tasks = await s.list_tasks("prj-1")
    assert [t["id"] for t in tasks] == [t1["id"], t2["id"], t3["id"]]
    await s.close()
