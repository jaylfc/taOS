"""Pure helpers for the Beads bridge.

No IO. No imports from other tinyagentos modules. Easy to test.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

_VERB_RE = re.compile(
    r"^/(claim|release|close)[ \t]+(tsk[-_][a-z0-9]+)(?:[ \t]+(.+))?$",
    flags=re.MULTILINE,
)
_TASK_ID_RE = re.compile(r"\btsk[-_][a-z0-9]+\b")
# /new "<title>" [@<assignee>]  — title may use single or double quotes
_NEW_RE = re.compile(
    r'^/new[ \t]+(?:"([^"]+)"|\'([^\']+)\')(?:[ \t]+@([A-Za-z0-9_-]+))?[ \t]*$',
    flags=re.MULTILINE,
)
_PRIORITY_MAP = {0: "p3", 1: "p2", 2: "p1"}


@dataclass
class NewVerb:
    title: str
    assignee: str | None  # Raw @-name, lowercased; None if not specified


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


def format_claimed(agent: str, tsk_id: str, title: str) -> str:
    return f'🤚 {agent} claimed {tsk_id} — "{title}"'


def format_released(agent: str, tsk_id: str, title: str) -> str:
    return f'↩️ {agent} released {tsk_id} — "{title}"'


def format_closed(agent: str, tsk_id: str, title: str, note: str | None) -> str:
    head = f'✅ {agent} closed {tsk_id} — "{title}"'
    if note and note.strip():
        return f"{head}\n{note.strip()}"
    return head


def format_ready(tsk_id: str, title: str, labels: list[str]) -> str:
    head = f'⚡ {tsk_id} ready — "{title}"'
    if labels:
        return f"{head} — {', '.join(labels)}"
    return head


def format_created(author: str, tsk_id: str, title: str) -> str:
    return f'✨ {author} created {tsk_id} — "{title}"'


Verb = Literal["claim", "release", "close"]


def parse_verbs(body: str) -> list[tuple[Verb, str, str | None]]:
    """Find lines matching `^/(claim|release|close) tsk[-_]<id>[ note]$`.

    Returns tuples in document order.
    """
    out: list[tuple[Verb, str, str | None]] = []
    for m in _VERB_RE.finditer(body or ""):
        verb = m.group(1)
        tsk = m.group(2)
        note = m.group(3)
        out.append((verb, tsk, note if note else None))  # type: ignore[arg-type]
    return out


def parse_new_verbs(body: str) -> list[NewVerb]:
    """Find lines matching `^/new "<title>" [@<assignee>]$`.

    Double- or single-quoted titles accepted. Malformed lines (no quotes,
    mismatched quotes) are silently skipped. Returns intents in document order.
    """
    out: list[NewVerb] = []
    for m in _NEW_RE.finditer(body or ""):
        # group 1 = double-quoted title, group 2 = single-quoted title
        title = m.group(1) or m.group(2)
        raw_assignee = m.group(3)
        out.append(NewVerb(
            title=title,
            assignee=raw_assignee.lower() if raw_assignee else None,
        ))
    return out


def scan_task_ids(body: str) -> list[str]:
    """Find all `\\btsk[-_][a-z0-9]+\\b` ids, deduped, order preserved."""
    seen: set[str] = set()
    out: list[str] = []
    for m in _TASK_ID_RE.finditer(body or ""):
        tid = m.group(0)
        if tid in seen:
            continue
        seen.add(tid)
        out.append(tid)
    return out
