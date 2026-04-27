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


from tinyagentos.projects.beads_format import (
    format_claimed,
    format_closed,
    format_ready,
    format_released,
)


def test_format_claimed():
    assert format_claimed("alice", "tsk_abc", "Wire OAuth") == (
        '🤚 alice claimed tsk_abc — "Wire OAuth"'
    )


def test_format_released():
    assert format_released("alice", "tsk_abc", "Wire OAuth") == (
        '↩️ alice released tsk_abc — "Wire OAuth"'
    )


def test_format_closed_without_note():
    assert format_closed("alice", "tsk_abc", "Wire OAuth", note=None) == (
        '✅ alice closed tsk_abc — "Wire OAuth"'
    )


def test_format_closed_with_note():
    assert format_closed("alice", "tsk_abc", "Wire OAuth", note="ship it") == (
        '✅ alice closed tsk_abc — "Wire OAuth"\nship it'
    )


def test_format_closed_strips_blank_note():
    assert format_closed("alice", "tsk_abc", "T", note="   ") == (
        '✅ alice closed tsk_abc — "T"'
    )


def test_format_ready_with_labels():
    assert format_ready("tsk_abc", "Wire OAuth", labels=["auth", "ui"]) == (
        '⚡ tsk_abc ready — "Wire OAuth" — auth, ui'
    )


def test_format_ready_no_labels():
    assert format_ready("tsk_abc", "Wire OAuth", labels=[]) == (
        '⚡ tsk_abc ready — "Wire OAuth"'
    )


from tinyagentos.projects.beads_format import parse_verbs, scan_task_ids


def test_parse_verbs_simple():
    assert parse_verbs("/claim tsk_abc") == [("claim", "tsk_abc", None)]


def test_parse_verbs_release():
    assert parse_verbs("/release tsk_abc") == [("release", "tsk_abc", None)]


def test_parse_verbs_close_with_note():
    assert parse_verbs("/close tsk_abc done shipping") == [
        ("close", "tsk_abc", "done shipping")
    ]


def test_parse_verbs_close_without_note():
    assert parse_verbs("/close tsk_abc") == [("close", "tsk_abc", None)]


def test_parse_verbs_multiple_lines():
    body = "/claim tsk_a\n/close tsk_b done\nstray"
    assert parse_verbs(body) == [
        ("claim", "tsk_a", None),
        ("close", "tsk_b", "done"),
    ]


def test_parse_verbs_indented_line_not_matched():
    assert parse_verbs("  /claim tsk_abc") == []


def test_parse_verbs_unknown_verb_not_matched():
    assert parse_verbs("/foo tsk_abc") == []


def test_parse_verbs_invalid_task_id_not_matched():
    assert parse_verbs("/claim notavalid") == []
    assert parse_verbs("/claim tsk_") == []  # must have at least one hex char


def test_parse_verbs_empty_body():
    assert parse_verbs("") == []


def test_scan_task_ids_finds_all():
    assert scan_task_ids("see tsk_abc and tsk_def for context") == [
        "tsk_abc",
        "tsk_def",
    ]


def test_scan_task_ids_dedupes_preserve_order():
    assert scan_task_ids("tsk_abc tsk_def tsk_abc") == ["tsk_abc", "tsk_def"]


def test_scan_task_ids_word_boundary():
    # xtsk_abc is not a match (no leading word boundary)
    assert scan_task_ids("xtsk_abc tsk_def") == ["tsk_def"]


def test_scan_task_ids_none_in_body():
    assert scan_task_ids("nothing here") == []
