from __future__ import annotations

from tinyagentos.projects.beads_format import (
    compute_ready,
    task_to_jsonl_dict,
)


def _task(**kw) -> dict:
    base = {
        "id": "tsk_a3f8c2",
        "project_id": "prj_1",
        "parent_task_id": None,
        "title": "T",
        "body": "",
        "status": "open",
        "priority": 1,
        "labels": ["x"],
        "assignee_id": None,
        "claimed_by": None,
        "claimed_at": None,
        "closed_at": None,
        "closed_by": None,
        "close_reason": None,
        "created_by": "u1",
        "created_at": 1000.0,
        "updated_at": 1000.0,
    }
    base.update(kw)
    return base


def test_task_to_jsonl_dict_no_relationships_no_assignee():
    t = _task(id="tsk_a", title="Hello")
    out = task_to_jsonl_dict(t, outbound_relationships=[], ready=True)
    assert out["id"] == "tsk_a"
    assert out["title"] == "Hello"
    assert out["status"] == "open"
    assert out["priority"] == "p2"  # priority int 1 -> "p2"
    assert out["labels"] == ["x"]
    assert out["assignee_ids"] == []
    assert out["parent_id"] is None
    assert out["deps"] == []
    assert out["ready"] is True


def test_task_to_jsonl_dict_with_assignee_and_parent():
    t = _task(id="tsk_b", assignee_id="agent_alice", parent_task_id="tsk_root")
    out = task_to_jsonl_dict(t, outbound_relationships=[], ready=False)
    assert out["assignee_ids"] == ["agent_alice"]
    assert out["parent_id"] == "tsk_root"


def test_task_to_jsonl_dict_with_relationships_preserves_order():
    t = _task(id="tsk_c")
    rels = [
        {"from_task_id": "tsk_c", "to_task_id": "tsk_x", "kind": "blocks"},
        {"from_task_id": "tsk_c", "to_task_id": "tsk_y", "kind": "relates_to"},
        {"from_task_id": "tsk_c", "to_task_id": "tsk_z", "kind": "blocks"},
    ]
    out = task_to_jsonl_dict(t, outbound_relationships=rels, ready=True)
    assert out["deps"] == [
        {"task_id": "tsk_x", "kind": "blocks"},
        {"task_id": "tsk_y", "kind": "relates_to"},
        {"task_id": "tsk_z", "kind": "blocks"},
    ]


def test_task_to_jsonl_dict_priority_clamped_to_p3():
    # priority ints map: 0→p3 (lowest), 1→p2, 2→p1, 3+→p0 (highest)
    assert task_to_jsonl_dict(_task(priority=0), [], False)["priority"] == "p3"
    assert task_to_jsonl_dict(_task(priority=1), [], False)["priority"] == "p2"
    assert task_to_jsonl_dict(_task(priority=2), [], False)["priority"] == "p1"
    assert task_to_jsonl_dict(_task(priority=3), [], False)["priority"] == "p0"
    assert task_to_jsonl_dict(_task(priority=99), [], False)["priority"] == "p0"


def test_compute_ready_open_no_blockers_is_ready():
    assert compute_ready(_task(status="open"), incoming_blocker_statuses=[]) is True


def test_compute_ready_open_with_open_blocker_not_ready():
    assert compute_ready(_task(status="open"), incoming_blocker_statuses=["open"]) is False


def test_compute_ready_open_with_only_closed_blockers_is_ready():
    assert (
        compute_ready(_task(status="open"), incoming_blocker_statuses=["closed", "closed"])
        is True
    )


def test_compute_ready_closed_task_never_ready():
    assert compute_ready(_task(status="closed"), incoming_blocker_statuses=[]) is False


def test_compute_ready_claimed_task_not_ready():
    assert compute_ready(_task(status="claimed"), incoming_blocker_statuses=[]) is False
