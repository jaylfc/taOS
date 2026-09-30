"""Pure dispatch policy — no I/O, no store, no app_state.

This module implements the dispatcher's selection logic as a pure function
suitable for unit testing and formal verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


OPEN_POOL = {"", "@any", "@all", "unassigned", "none", None}


def is_candidate(task: dict[str, Any]) -> tuple[bool, str]:
    """Check if a task is a candidate for dispatch.

    Returns (ok, reason) where reason is one of:
      - "ok"
      - "not_claimable_label"
      - "assigned_elsewhere"
      - "held"
      - "blocked_label"
    """
    labels = task.get("labels", []) or []

    # Must have claimable or fleet:claimable label (case-insensitive)
    has_claimable = any(
        lbl.strip().lower() in ("claimable", "fleet:claimable") for lbl in labels
    )
    if not has_claimable:
        return False, "not_claimable_label"

    # Assignee must be in OPEN_POOL
    assignee_id = task.get("assignee_id")
    if assignee_id not in OPEN_POOL:
        return False, "assigned_elsewhere"

    # No dispatch:hold label
    if any(lbl.strip().lower() == "dispatch:hold" for lbl in labels):
        return False, "held"

    # No blocked:<x> label (fails CLOSED; blocked-on: is ready_tasks view's job)
    if any(lbl.strip().lower().startswith("blocked:") for lbl in labels):
        return False, "blocked_label"

    return True, "ok"


def is_board_dispatchable(project: dict[str, Any]) -> bool:
    """Check if a board (project) is dispatchable.

    Returns False when project settings carry the board-level hold:
    settings.get("dispatch") == "hold"
    """
    settings = project.get("settings", {}) or {}
    return settings.get("dispatch") != "hold"


@dataclass(frozen=True)
class Assignment:
    task_id: str
    project_id: str
    canonical_id: str
    reason: str


def select_assignments(
    candidates: list[dict[str, Any]],
    agents: list[dict[str, Any]],
    grants: dict[str, list[str]],
    load: dict[str, int],
    board_inflight: dict[str, int],
    last_assigned_at: dict[str, float],
    recent_expiries: dict[str, tuple[str, ...]],
    card_expiry_counts: dict[str, int],
    cap: int,
) -> list[Assignment]:
    """Select assignments for candidates to agents.

    Pure function: never mutates caller's dicts. Works on local copies.

    Rules:
      - board_served[p] = board_inflight.get(p, 0) > 0 at entry (local copy)
      - Loop: among remaining candidates that still have an eligible agent,
        pick next card by (board_served[project], -priority, card_expiry_counts.get(id,0), created_at)
        False sorts first (unserved boards first)
      - Per card: pool = agents with card.project_id in grants[agent] AND
        load[agent] < cap AND agent not in recent_expiries.get(card.id, ())
        If anti-affinity filter alone emptied pool, retry without it.
      - Pick: min by (load, last_assigned_at, canonical_id)
      - After pick: load[agent] += 1 and board_served[project] = True on LOCAL copies
      - Card with no eligible agent is skipped; continue
    """
    # Local copies — never mutate caller's dicts
    board_served = {p: board_inflight.get(p, 0) > 0 for p in board_inflight}
    # Ensure all candidate projects are in board_served
    for c in candidates:
        pid = c.get("project_id")
        if pid and pid not in board_served:
            board_served[pid] = False

    load_local = dict(load)
    assignments: list[Assignment] = []

    # Filter candidates to only those that are dispatchable
    eligible_candidates = []
    for c in candidates:
        ok, reason = is_candidate(c)
        if ok:
            # Also check board dispatchable
            pid = c.get("project_id")
            if pid:
                project = {"settings": {}}  # minimal project for check
                # We don't have full project here, assume dispatchable unless board_inflight says otherwise
                # The board hold check would need project settings passed in
                # For now, we rely on the caller to filter non-dispatchable boards
                pass
            eligible_candidates.append(c)

    # Build agent index for faster lookup
    agent_by_id = {a["id"]: a for a in agents}

    # Main assignment loop
    remaining = list(eligible_candidates)

    while remaining:
        # Find the best candidate according to the sort key
        # (board_served[project], -priority, card_expiry_counts.get(id,0), created_at)
        # False (0) sorts before True (1) for board_served
        best_idx = None
        best_key = None

        for i, c in enumerate(remaining):
            pid = c.get("project_id", "")
            served = board_served.get(pid, False)
            priority = c.get("priority", 0)
            expiry_count = card_expiry_counts.get(c.get("id", ""), 0)
            created_at = c.get("created_at", 0.0)

            key = (served, -priority, expiry_count, created_at)

            if best_key is None or key < best_key:
                # Check if this candidate has any eligible agent
                if _has_eligible_agent(c, agents, grants, load_local, cap, recent_expiries):
                    best_key = key
                    best_idx = i

        if best_idx is None:
            # No remaining candidate has an eligible agent
            break

        # Pick the best candidate
        card = remaining.pop(best_idx)
        card_id = card.get("id", "")
        pid = card.get("project_id", "")

        # Find eligible agents for this card
        eligible_agents = _get_eligible_agents(card, agents, grants, load_local, cap, recent_expiries)

        if not eligible_agents:
            # No eligible agent even after anti-affinity fallback (shouldn't happen due to check above)
            continue

        # Pick agent: min by (load, last_assigned_at, canonical_id)
        def agent_key(a):
            aid = a["id"]
            return (
                load_local.get(aid, 0),
                last_assigned_at.get(aid, 0.0),
                aid,
            )

        chosen_agent = min(eligible_agents, key=agent_key)
        chosen_aid = chosen_agent["id"]

        # Create assignment
        assignments.append(Assignment(
            task_id=card_id,
            project_id=pid,
            canonical_id=chosen_aid,
            reason="ok",
        ))

        # Update local state
        load_local[chosen_aid] = load_local.get(chosen_aid, 0) + 1
        board_served[pid] = True

    return assignments


def _has_eligible_agent(
    card: dict[str, Any],
    agents: list[dict[str, Any]],
    grants: dict[str, list[str]],
    load: dict[str, int],
    cap: int,
    recent_expiries: dict[str, tuple[str, ...]],
) -> bool:
    """Check if card has at least one eligible agent (with anti-affinity fallback)."""
    pid = card.get("project_id", "")
    card_id = card.get("id", "")
    recent = recent_expiries.get(card_id, ())

    # First pass: with anti-affinity
    for a in agents:
        aid = a["id"]
        agent_grants = grants.get(aid, [])
        if pid not in agent_grants:
            continue
        if load.get(aid, 0) >= cap:
            continue
        if aid in recent:
            continue
        return True

    # Fallback: without anti-affinity
    for a in agents:
        aid = a["id"]
        agent_grants = grants.get(aid, [])
        if pid not in agent_grants:
            continue
        if load.get(aid, 0) >= cap:
            continue
        return True

    return False


def _get_eligible_agents(
    card: dict[str, Any],
    agents: list[dict[str, Any]],
    grants: dict[str, list[str]],
    load: dict[str, int],
    cap: int,
    recent_expiries: dict[str, tuple[str, ...]],
) -> list[dict[str, Any]]:
    """Get eligible agents for a card, with anti-affinity fallback."""
    pid = card.get("project_id", "")
    card_id = card.get("id", "")
    recent = recent_expiries.get(card_id, ())

    # First pass: with anti-affinity
    eligible = []
    for a in agents:
        aid = a["id"]
        agent_grants = grants.get(aid, [])
        if pid not in agent_grants:
            continue
        if load.get(aid, 0) >= cap:
            continue
        if aid in recent:
            continue
        eligible.append(a)

    if eligible:
        return eligible

    # Fallback: without anti-affinity
    eligible = []
    for a in agents:
        aid = a["id"]
        agent_grants = grants.get(aid, [])
        if pid not in agent_grants:
            continue
        if load.get(aid, 0) >= cap:
            continue
        eligible.append(a)

    return eligible