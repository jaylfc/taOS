"""Pure helpers for the Beads bridge.

No IO. No imports from other tinyagentos modules. Easy to test.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Iterable, Literal


_VERB_RE = re.compile(
    r"^/(claim|release|close)\s+(tsk_[a-z0-9]+)(?:\s+(.+))?$",
    flags=re.MULTILINE,
)
_TASK_ID_RE = re.compile(r"\btsk_[a-z0-9]+\b")
_PRIORITY_MAP = {0: "p3", 1: "p2", 2: "p1"}


def _isoformat(ts: float | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def task_to_jsonl_dict(
    task: dict, outbound_relationships: list[dict], ready: bool
) -> dict:
    """Map a ProjectTaskStore row + its outbound relationships to the
    JSONL schema described in the spec §4.2."""
    pri_int = int(task.get("priority", 0))
    priority = _PRIORITY_MAP.get(pri_int, "p0")
    assignee_id = task.get("assignee_id")
    return {
        "id": task["id"],
        "title": task.get("title", ""),
        "description": task.get("body", ""),
        "status": task.get("status", "open"),
        "priority": priority,
        "labels": list(task.get("labels") or []),
        "assignee_ids": [assignee_id] if assignee_id else [],
        "parent_id": task.get("parent_task_id"),
        "deps": [
            {"task_id": r["to_task_id"], "kind": r["kind"]}
            for r in outbound_relationships
        ],
        "ready": bool(ready),
        "created_at": _isoformat(task.get("created_at")),
        "updated_at": _isoformat(task.get("updated_at")),
    }


def compute_ready(task: dict, incoming_blocker_statuses: list[str]) -> bool:
    """`ready` iff status is open AND every incoming `blocks` edge points
    from a task whose status is `closed` (or `cancelled`)."""
    if task.get("status") != "open":
        return False
    return all(s in ("closed", "cancelled") for s in incoming_blocker_statuses)
