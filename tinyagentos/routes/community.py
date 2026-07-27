"""Community View routes — read-only project projection for collaborators.

Serves a scoped, allowlisted projection of a project to remote human
collaborators over the peer channel.  The projection is read-only and bounded:
no files, memory, secrets, canvas payloads, or non-community chat are exposed.

Endpoints
---------
GET /api/projects/{project_id}/community/snapshot  — full community projection
GET /api/projects/{project_id}/community/stats     — stats + leaderboard
"""

from __future__ import annotations

import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request

from tinyagentos.auth_context import CurrentUser, current_user

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Allowlist: board-task fields exposed in the community view
# ---------------------------------------------------------------------------
_TASK_ALLOWLIST = frozenset({
    "id", "title", "status", "priority", "labels",
    "claimed_by", "claimed_at", "closed_at", "created_at", "updated_at",
})

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_task(task: dict) -> dict:
    """Strip non-allowlisted fields from a task row for the community projection."""
    return {k: v for k, v in task.items() if k in _TASK_ALLOWLIST}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/api/projects/{project_id}/community/snapshot")
async def community_snapshot(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Return the full community projection for a project.

    Only the project owner (or a delegated peer-token-authenticated contact
    who is a human member of the project) may access this endpoint.

    The response is an allowlisted projection:
      - project metadata (name, slug, description, status)
      - board tasks (id, title, status, claimant, labels, timestamps)
      - contribution stats (per-contributor claim/close counts, leaderboard)
      - recent activity feed (last 500 board-audit events, rendered as most-recent 20)
    """
    project_store = request.app.state.project_store
    task_store = request.app.state.project_task_store
    board_audit = request.app.state.board_audit

    # Authorize: must be the project owner.
    project = await project_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if not user.is_admin and project.get("user_id") != user.user_id:
        raise HTTPException(status_code=404, detail="project not found")

    # Project metadata (allowlisted subset).
    meta = {
        "id": project.get("id"),
        "name": project.get("name"),
        "slug": project.get("slug"),
        "description": project.get("description"),
        "status": project.get("status"),
    }

    # Board tasks — all statuses, sanitized.
    all_tasks = await task_store.list_tasks(project_id=project_id)
    tasks = [_sanitize_task(t) for t in all_tasks]

    # Task breakdown by status.
    status_counts: dict[str, int] = defaultdict(int)
    for t in tasks:
        status_counts[t.get("status", "unknown")] += 1

    # Contribution rollup from board-audit events.
    audit_events = await board_audit.recent_for_project(project_id, limit=500)
    contributor_stats = _build_leaderboard(audit_events)

    # Recent activity feed (lightweight, last 20 events).
    feed = [
        {
            "id": e["id"],
            "task_id": e["task_id"],
            "event": e["event"],
            "actor": e["actor"],
            "ts": e["ts"],
        }
        for e in audit_events[:20]
    ]

    return {
        "project": meta,
        "tasks": tasks,
        "status_counts": dict(status_counts),
        "contributors": contributor_stats,
        "recent_activity": feed,
    }


@router.get("/api/projects/{project_id}/community/stats")
async def community_stats(
    project_id: str,
    request: Request,
    user: CurrentUser = Depends(current_user),
):
    """Return stats + leaderboard for the community view (lighter than snapshot).

    Suitable for polling or dashboard widgets — omits the full task list
    and feed that the snapshot endpoint includes.
    """
    project_store = request.app.state.project_store
    task_store = request.app.state.project_task_store
    board_audit = request.app.state.board_audit

    project = await project_store.get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    if not user.is_admin and project.get("user_id") != user.user_id:
        raise HTTPException(status_code=404, detail="project not found")

    # Status counts.
    all_tasks = await task_store.list_tasks(project_id=project_id)
    status_counts: dict[str, int] = defaultdict(int)
    for t in all_tasks:
        status_counts[t.get("status", "unknown")] += 1

    # Leaderboard.
    audit_events = await board_audit.recent_for_project(project_id, limit=500)
    leaderboard = _build_leaderboard(audit_events)

    return {
        "project_id": project_id,
        "project_name": project.get("name"),
        "status_counts": dict(status_counts),
        "leaderboard": leaderboard,
    }


# ---------------------------------------------------------------------------
# Leaderboard rollup
# ---------------------------------------------------------------------------


def _build_leaderboard(events: list[dict]) -> list[dict]:
    """Build a per-contributor leaderboard from board-audit events.

    Aggregates claim + close counts per actor.  A contributor's line includes
    all their sponsored agents (future: aggregate by sponsor_contact_id).

    Returns a list sorted by total events descending.
    """
    stats: dict[str, dict[str, int]] = defaultdict(
        lambda: {"claims": 0, "closes": 0}
    )
    for ev in events:
        actor = ev.get("actor", "")
        event = ev.get("event", "")
        if not actor:
            continue
        if event in ("task.claimed", "claimed"):
            stats[actor]["claims"] += 1
        elif event in ("task.closed", "task.completed", "closed", "completed"):
            stats[actor]["closes"] += 1

    leaderboard = [
        {
            "actor": actor,
            "claims": s["claims"],
            "closes": s["closes"],
            "total": s["claims"] + s["closes"],
        }
        for actor, s in stats.items()
    ]
    leaderboard.sort(key=lambda x: x["total"], reverse=True)
    return leaderboard
