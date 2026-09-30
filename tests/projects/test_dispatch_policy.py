"""Tests for dispatch_policy.py — pure policy, no I/O, plain dicts only."""

import pytest

from tinyagentos.projects.dispatch_policy import (
    OPEN_POOL,
    is_candidate,
    is_board_dispatchable,
    Assignment,
    select_assignments,
)


class TestIsCandidate:
    def test_claimable_label_accepted(self):
        task = {
            "id": "tsk-1",
            "project_id": "prj-1",
            "labels": ["claimable"],
            "assignee_id": "",
            "priority": 10,
            "created_at": 1000.0,
        }
        ok, reason = is_candidate(task)
        assert ok is True
        assert reason == "ok"

    def test_fleet_claimable_accepted(self):
        task = {
            "id": "tsk-1",
            "project_id": "prj-1",
            "labels": ["fleet:claimable"],
            "assignee_id": "",
            "priority": 10,
            "created_at": 1000.0,
        }
        ok, reason = is_candidate(task)
        assert ok is True
        assert reason == "ok"

    def test_claimable_case_insensitive(self):
        task = {
            "id": "tsk-1",
            "project_id": "prj-1",
            "labels": ["CLAIMABLE"],
            "assignee_id": "",
            "priority": 10,
            "created_at": 1000.0,
        }
        ok, reason = is_candidate(task)
        assert ok is True
        assert reason == "ok"

    def test_fleet_claimable_case_insensitive(self):
        task = {
            "id": "tsk-1",
            "project_id": "prj-1",
            "labels": ["FLEET:CLAIMABLE"],
            "assignee_id": "",
            "priority": 10,
            "created_at": 1000.0,
        }
        ok, reason = is_candidate(task)
        assert ok is True
        assert reason == "ok"

    def test_not_claimable_label_rejected(self):
        task = {
            "id": "tsk-1",
            "project_id": "prj-1",
            "labels": ["other"],
            "assignee_id": "",
            "priority": 10,
            "created_at": 1000.0,
        }
        ok, reason = is_candidate(task)
        assert ok is False
        assert reason == "not_claimable_label"

    def test_specific_assignee_not_a_candidate(self):
        task = {
            "id": "tsk-1",
            "project_id": "prj-1",
            "labels": ["claimable"],
            "assignee_id": "agent-specific",
            "priority": 10,
            "created_at": 1000.0,
        }
        ok, reason = is_candidate(task)
        assert ok is False
        assert reason == "assigned_elsewhere"

    def test_open_pool_assignee_empty_string_ok(self):
        task = {
            "id": "tsk-1",
            "project_id": "prj-1",
            "labels": ["claimable"],
            "assignee_id": "",
            "priority": 10,
            "created_at": 1000.0,
        }
        ok, reason = is_candidate(task)
        assert ok is True
        assert reason == "ok"

    def test_open_pool_assignee_none_ok(self):
        task = {
            "id": "tsk-1",
            "project_id": "prj-1",
            "labels": ["claimable"],
            "assignee_id": None,
            "priority": 10,
            "created_at": 1000.0,
        }
        ok, reason = is_candidate(task)
        assert ok is True
        assert reason == "ok"

    def test_open_pool_assignee_unassigned_ok(self):
        task = {
            "id": "tsk-1",
            "project_id": "prj-1",
            "labels": ["claimable"],
            "assignee_id": "unassigned",
            "priority": 10,
            "created_at": 1000.0,
        }
        ok, reason = is_candidate(task)
        assert ok is True
        assert reason == "ok"

    def test_open_pool_assignee_none_value_ok(self):
        task = {
            "id": "tsk-1",
            "project_id": "prj-1",
            "labels": ["claimable"],
            "assignee_id": "none",
            "priority": 10,
            "created_at": 1000.0,
        }
        ok, reason = is_candidate(task)
        assert ok is True
        assert reason == "ok"

    def test_open_pool_assignee_at_any_ok(self):
        task = {
            "id": "tsk-1",
            "project_id": "prj-1",
            "labels": ["claimable"],
            "assignee_id": "@any",
            "priority": 10,
            "created_at": 1000.0,
        }
        ok, reason = is_candidate(task)
        assert ok is True
        assert reason == "ok"

    def test_open_pool_assignee_at_all_ok(self):
        task = {
            "id": "tsk-1",
            "project_id": "prj-1",
            "labels": ["claimable"],
            "assignee_id": "@all",
            "priority": 10,
            "created_at": 1000.0,
        }
        ok, reason = is_candidate(task)
        assert ok is True
        assert reason == "ok"

    def test_dispatch_hold_excluded(self):
        task = {
            "id": "tsk-1",
            "project_id": "prj-1",
            "labels": ["claimable", "dispatch:hold"],
            "assignee_id": "",
            "priority": 10,
            "created_at": 1000.0,
        }
        ok, reason = is_candidate(task)
        assert ok is False
        assert reason == "held"

    def test_blocked_label_fails_closed(self):
        task = {
            "id": "tsk-1",
            "project_id": "prj-1",
            "labels": ["claimable", "blocked:some-reason"],
            "assignee_id": "",
            "priority": 10,
            "created_at": 1000.0,
        }
        ok, reason = is_candidate(task)
        assert ok is False
        assert reason == "blocked_label"

    def test_blocked_on_label_is_ok_not_checked_here(self):
        """blocked-on: is the ready_tasks view's job, not is_candidate's."""
        task = {
            "id": "tsk-1",
            "project_id": "prj-1",
            "labels": ["claimable", "blocked-on:tsk-other"],
            "assignee_id": "",
            "priority": 10,
            "created_at": 1000.0,
        }
        ok, reason = is_candidate(task)
        assert ok is True
        assert reason == "ok"


class TestIsBoardDispatchable:
    def test_default_dispatchable(self):
        project = {"settings": {}}
        assert is_board_dispatchable(project) is True

    def test_dispatch_hold_setting_excluded(self):
        project = {"settings": {"dispatch": "hold"}}
        assert is_board_dispatchable(project) is False

    def test_other_dispatch_setting_ok(self):
        project = {"settings": {"dispatch": "auto"}}
        assert is_board_dispatchable(project) is True


class TestSelectAssignments:
    def _make_task(self, tid, project_id, priority, created_at, labels=None, assignee_id=""):
        return {
            "id": tid,
            "project_id": project_id,
            "priority": priority,
            "created_at": created_at,
            "labels": labels or ["claimable"],
            "assignee_id": assignee_id,
        }

    def _make_agent(self, aid, grants, load=0):
        return {"id": aid, "grants": grants, "load": load}

    def test_unserved_board_first(self):
        """Board X has three p90 cards, board Y one p10 card, two idle agents
        granted on both, cap 1 -> one X card (oldest p90) AND the Y card assigned.
        Pooled priority would give both to X; this test must fail on pooled impl."""
        candidates = [
            self._make_task("tsk-x1", "prj-X", 90, 1000.0),
            self._make_task("tsk-x2", "prj-X", 90, 1001.0),
            self._make_task("tsk-x3", "prj-X", 90, 1002.0),
            self._make_task("tsk-y1", "prj-Y", 10, 2000.0),
        ]
        agents = [
            {"id": "agent-1", "grants": ["prj-X", "prj-Y"], "load": 0},
            {"id": "agent-2", "grants": ["prj-X", "prj-Y"], "load": 0},
        ]
        grants = {"agent-1": ["prj-X", "prj-Y"], "agent-2": ["prj-X", "prj-Y"]}
        load = {"agent-1": 0, "agent-2": 0}
        board_inflight = {}
        last_assigned_at = {"agent-1": 0.0, "agent-2": 0.0}
        recent_expiries = {}
        card_expiry_counts = {}
        cap = 1

        assignments = select_assignments(
            candidates, agents, grants, load, board_inflight,
            last_assigned_at, recent_expiries, card_expiry_counts, cap
        )

        assigned_ids = {a.task_id for a in assignments}
        assert "tsk-y1" in assigned_ids, "Unserved board Y's card must be assigned"
        assert len(assigned_ids) == 2, "Two agents, cap 1 -> 2 assignments"
        # One X card (the oldest p90) and Y card
        assert "tsk-x1" in assigned_ids or "tsk-x2" in assigned_ids or "tsk-x3" in assigned_ids

    def test_board_with_inflight_work_ranks_after_unserved_board(self):
        """Board with inflight work should rank after unserved board."""
        candidates = [
            self._make_task("tsk-x1", "prj-X", 90, 1000.0),
            self._make_task("tsk-y1", "prj-Y", 10, 2000.0),
        ]
        agents = [
            {"id": "agent-1", "grants": ["prj-X", "prj-Y"], "load": 0},
            {"id": "agent-2", "grants": ["prj-X", "prj-Y"], "load": 0},
        ]
        grants = {"agent-1": ["prj-X", "prj-Y"], "agent-2": ["prj-X", "prj-Y"]}
        load = {"agent-1": 0, "agent-2": 0}
        # prj-X already has inflight work
        board_inflight = {"prj-X": 1, "prj-Y": 0}
        last_assigned_at = {"agent-1": 0.0, "agent-2": 0.0}
        recent_expiries = {}
        card_expiry_counts = {}
        cap = 1

        assignments = select_assignments(
            candidates, agents, grants, load, board_inflight,
            last_assigned_at, recent_expiries, card_expiry_counts, cap
        )

        assigned_ids = {a.task_id for a in assignments}
        # prj-Y is unserved (0 inflight), so its card should be picked first
        # But we have 2 agents, so both should get assigned
        assert len(assigned_ids) == 2
        # The first pick should be from prj-Y (unserved board)
        first_assignment = assignments[0]
        assert first_assignment.project_id == "prj-Y"

    def test_within_tier_priority_then_untried_then_oldest(self):
        """Within same board_served tier: priority desc, then untried before retried, then oldest."""
        candidates = [
            self._make_task("tsk-a", "prj-1", 50, 1000.0),  # lower priority, older
            self._make_task("tsk-b", "prj-1", 100, 1001.0),  # higher priority, newer
            self._make_task("tsk-c", "prj-1", 100, 1000.0),  # higher priority, oldest
        ]
        agents = [{"id": "agent-1", "grants": ["prj-1"], "load": 0}]
        grants = {"agent-1": ["prj-1"]}
        load = {"agent-1": 0}
        board_inflight = {"prj-1": 0}
        last_assigned_at = {"agent-1": 0.0}
        recent_expiries = {}
        card_expiry_counts = {"tsk-a": 1, "tsk-b": 0, "tsk-c": 0}  # a has been tried once
        cap = 1

        assignments = select_assignments(
            candidates, agents, grants, load, board_inflight,
            last_assigned_at, recent_expiries, card_expiry_counts, cap
        )

        assert len(assignments) == 1
        # tsk-c and tsk-b have same priority (100), both untried (expiry_count 0)
        # tsk-c is older (1000.0 vs 1001.0), so it should win
        assert assignments[0].task_id == "tsk-c"

    def test_grant_checked_per_card_board(self):
        """A granted on prj-1 only, one card on prj-2 -> []; PR #2930 red 1."""
        candidates = [self._make_task("tsk-1", "prj-2", 10, 1000.0)]
        agents = [{"id": "agent-1", "grants": ["prj-1"], "load": 0}]
        grants = {"agent-1": ["prj-1"]}
        load = {"agent-1": 0}
        board_inflight = {"prj-2": 0}
        last_assigned_at = {"agent-1": 0.0}
        recent_expiries = {}
        card_expiry_counts = {}
        cap = 1

        assignments = select_assignments(
            candidates, agents, grants, load, board_inflight,
            last_assigned_at, recent_expiries, card_expiry_counts, cap
        )

        assert assignments == []

    def test_load_increments_within_one_call(self):
        """Two idle agents, three cards -> 2 DIFFERENT agents; #2930 red 2."""
        candidates = [
            self._make_task("tsk-1", "prj-1", 10, 1000.0),
            self._make_task("tsk-2", "prj-1", 10, 1001.0),
            self._make_task("tsk-3", "prj-1", 10, 1002.0),
        ]
        agents = [
            {"id": "agent-1", "grants": ["prj-1"], "load": 0},
            {"id": "agent-2", "grants": ["prj-1"], "load": 0},
        ]
        grants = {"agent-1": ["prj-1"], "agent-2": ["prj-1"]}
        load = {"agent-1": 0, "agent-2": 0}
        board_inflight = {"prj-1": 0}
        last_assigned_at = {"agent-1": 0.0, "agent-2": 0.0}
        recent_expiries = {}
        card_expiry_counts = {}
        cap = 1

        assignments = select_assignments(
            candidates, agents, grants, load, board_inflight,
            last_assigned_at, recent_expiries, card_expiry_counts, cap
        )

        assigned_agents = {a.canonical_id for a in assignments}
        assert len(assigned_agents) == 2, "Two different agents should be assigned"
        assert len(assignments) == 2, "Two assignments max with cap=1 and 2 agents"

    def test_anti_affinity_prefers_other_agent_and_falls_back_when_alone(self):
        """Anti-affinity: prefer agent not in recent_expiries, fall back if only option."""
        candidates = [
            self._make_task("tsk-1", "prj-1", 10, 1000.0),
        ]
        agents = [
            {"id": "agent-1", "grants": ["prj-1"], "load": 0},
            {"id": "agent-2", "grants": ["prj-1"], "load": 0},
        ]
        grants = {"agent-1": ["prj-1"], "agent-2": ["prj-1"]}
        load = {"agent-1": 0, "agent-2": 0}
        board_inflight = {"prj-1": 0}
        last_assigned_at = {"agent-1": 0.0, "agent-2": 0.0}
        # agent-1 recently expired on this card, agent-2 hasn't
        recent_expiries = {"tsk-1": ("agent-1",)}
        card_expiry_counts = {}
        cap = 1

        assignments = select_assignments(
            candidates, agents, grants, load, board_inflight,
            last_assigned_at, recent_expiries, card_expiry_counts, cap
        )

        assert len(assignments) == 1
        assert assignments[0].canonical_id == "agent-2"

    def test_anti_affinity_fallback_when_alone(self):
        """When only agent available is in recent_expiries, still assign (fallback)."""
        candidates = [
            self._make_task("tsk-1", "prj-1", 10, 1000.0),
        ]
        agents = [{"id": "agent-1", "grants": ["prj-1"], "load": 0}]
        grants = {"agent-1": ["prj-1"]}
        load = {"agent-1": 0}
        board_inflight = {"prj-1": 0}
        last_assigned_at = {"agent-1": 0.0}
        recent_expiries = {"tsk-1": ("agent-1",)}
        card_expiry_counts = {}
        cap = 1

        assignments = select_assignments(
            candidates, agents, grants, load, board_inflight,
            last_assigned_at, recent_expiries, card_expiry_counts, cap
        )

        assert len(assignments) == 1
        assert assignments[0].canonical_id == "agent-1"

    def test_caller_dicts_not_mutated(self):
        """select_assignments must not mutate caller's dicts (load, board_inflight)."""
        candidates = [
            self._make_task("tsk-1", "prj-1", 10, 1000.0),
            self._make_task("tsk-2", "prj-1", 10, 1001.0),
        ]
        agents = [
            {"id": "agent-1", "grants": ["prj-1"], "load": 0},
            {"id": "agent-2", "grants": ["prj-1"], "load": 0},
        ]
        grants = {"agent-1": ["prj-1"], "agent-2": ["prj-1"]}
        load = {"agent-1": 0, "agent-2": 0}
        board_inflight = {"prj-1": 0}
        last_assigned_at = {"agent-1": 0.0, "agent-2": 0.0}
        recent_expiries = {}
        card_expiry_counts = {}
        cap = 1

        original_load = dict(load)
        original_board_inflight = dict(board_inflight)

        select_assignments(
            candidates, agents, grants, load, board_inflight,
            last_assigned_at, recent_expiries, card_expiry_counts, cap
        )

        assert load == original_load, "load dict must not be mutated"
        assert board_inflight == original_board_inflight, "board_inflight dict must not be mutated"


class TestAssignmentDataclass:
    def test_assignment_fields(self):
        a = Assignment(
            task_id="tsk-1",
            project_id="prj-1",
            canonical_id="agent-1",
            reason="ok"
        )
        assert a.task_id == "tsk-1"
        assert a.project_id == "prj-1"
        assert a.canonical_id == "agent-1"
        assert a.reason == "ok"

    def test_assignment_frozen(self):
        a = Assignment(
            task_id="tsk-1",
            project_id="prj-1",
            canonical_id="agent-1",
            reason="ok"
        )
        with pytest.raises(Exception):
            a.task_id = "tsk-2"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])