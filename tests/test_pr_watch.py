import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

from pr_watch import classify, process_tick


REQUIRED_CHECKS = [
    {"name": "test (3.1", "conclusion": "SUCCESS"},
    {"name": "lint", "conclusion": "SUCCESS"},
    {"name": "spa-build", "conclusion": "SUCCESS"},
    {"name": "shards", "conclusion": "SUCCESS"},
]


class TestClassify:
    def test_red_on_failure(self):
        pr = {
            "statusCheckRollup": [
                {"name": "test (3.1", "conclusion": "FAILURE"},
            ],
            "mergeStateStatus": "CLEAN",
        }
        assert classify(pr) == "RED"

    def test_red_on_timed_out(self):
        pr = {
            "statusCheckRollup": [
                {"name": "test (3.1", "conclusion": "TIMED_OUT"},
            ],
            "mergeStateStatus": "CLEAN",
        }
        assert classify(pr) == "RED"

    def test_red_on_action_required(self):
        pr = {
            "statusCheckRollup": [
                {"name": "test (3.1", "conclusion": "ACTION_REQUIRED"},
            ],
            "mergeStateStatus": "CLEAN",
        }
        assert classify(pr) == "RED"

    def test_conflict_on_dirty(self):
        pr = {
            "statusCheckRollup": [],
            "mergeStateStatus": "DIRTY",
        }
        assert classify(pr) == "CONFLICT"

    def test_ready_when_all_green(self):
        pr = {
            "statusCheckRollup": REQUIRED_CHECKS,
            "mergeStateStatus": "CLEAN",
        }
        assert classify(pr) == "READY"

    def test_pending_when_not_all_green(self):
        pr = {
            "statusCheckRollup": [
                {"name": "test (3.1", "conclusion": "SUCCESS"},
                {"name": "lint", "conclusion": "PENDING"},
            ],
            "mergeStateStatus": "CLEAN",
        }
        assert classify(pr) == "PENDING"

    def test_pending_when_no_required_checks(self):
        pr = {
            "statusCheckRollup": [
                {"name": "other-check", "conclusion": "SUCCESS"},
            ],
            "mergeStateStatus": "CLEAN",
        }
        assert classify(pr) == "PENDING"


def make_pr(number, title, login, is_draft, head_oid, checks, merge_state):
    return {
        "number": number,
        "title": title,
        "author": {"login": login},
        "isDraft": is_draft,
        "headRefOid": head_oid,
        "statusCheckRollup": checks,
        "mergeStateStatus": merge_state,
    }


class TestDraftTransitions:
    def test_drafted_instead_of_gone(self):
        seen = {1: "READY"}
        reported = {}
        prs = [make_pr(1, "Add feature", "jaylfc", True, "abc12345", [], "CLEAN")]
        messages, live, reported, _ = process_tick(prs, seen, reported, False)
        assert messages == ["PR #1 DRAFTED | Add feature"]
        assert live == {1: "DRAFT"}
        assert reported == {1: ("DRAFT", "abc12345")}

    def test_ready_from_draft(self):
        seen = {1: "DRAFT"}
        reported = {1: ("DRAFT", "abc12345")}
        prs = [make_pr(1, "Add feature", "jaylfc", False, "def98765", REQUIRED_CHECKS, "CLEAN")]
        messages, live, reported, _ = process_tick(prs, seen, reported, False)
        assert messages == ["PR #1 READY-FROM-DRAFT | Add feature"]
        assert live == {1: "READY"}
        assert reported == {1: ("READY", "def98765")}

    def test_gone_when_closed(self):
        seen = {1: "READY"}
        reported = {}
        prs = []
        messages, live, reported, _ = process_tick(prs, seen, reported, False)
        assert messages == ["PR #1 GONE (merged or closed) - throttle slot freed"]
        assert live == {}
        assert reported == {}

    def test_draft_first_sighting_no_new_on_first_pass(self):
        seen = {}
        reported = {}
        prs = [make_pr(1, "Add feature", "jaylfc", True, "abc12345", [], "CLEAN")]
        messages, live, reported, _ = process_tick(prs, seen, reported, True)
        assert messages == []
        assert live == {1: "DRAFT"}
        assert reported == {}

    def test_new_message_after_first_pass(self):
        seen = {}
        reported = {}
        prs = [make_pr(1, "Add feature", "jaylfc", False, "abc12345", REQUIRED_CHECKS, "CLEAN")]
        messages, live, reported, _ = process_tick(prs, seen, reported, False)
        assert messages == ["PR #1 NEW [READY] Add feature"]
        assert live == {1: "READY"}
        assert reported == {1: ("READY", "abc12345")}

    def test_no_duplicate_transition_same_commit(self):
        seen = {1: "READY"}
        reported = {1: ("READY", "abc1234")}
        prs = [make_pr(1, "Add feature", "jaylfc", False, "abc123456789", REQUIRED_CHECKS, "CLEAN")]
        messages, live, reported, _ = process_tick(prs, seen, reported, False)
        assert messages == []
        assert live == {1: "READY"}
        assert reported == {1: ("READY", "abc1234")}

    def test_draft_remains_draft_silent(self):
        seen = {1: "DRAFT"}
        reported = {1: ("DRAFT", "abc1234")}
        prs = [make_pr(1, "Add feature", "jaylfc", True, "abc123456789", [], "CLEAN")]
        messages, live, reported, _ = process_tick(prs, seen, reported, False)
        assert messages == []
        assert live == {1: "DRAFT"}
        assert reported == {1: ("DRAFT", "abc1234")}

    def test_author_filter(self):
        seen = {}
        reported = {}
        prs = [make_pr(1, "Add feature", "other-user", False, "abc123456789", REQUIRED_CHECKS, "CLEAN")]
        messages, live, reported, _ = process_tick(prs, seen, reported, False)
        assert messages == []
        assert live == {}
        assert reported == {}

    def test_normal_red_transition(self):
        seen = {1: "READY"}
        reported = {1: ("READY", "abc12345")}
        prs = [make_pr(1, "Add feature", "jaylfc", False, "def98765", REQUIRED_CHECKS, "CLEAN")]
        prs[0]["statusCheckRollup"] = [
            {"name": "test (3.1", "conclusion": "FAILURE"},
        ]
        messages, live, reported, _ = process_tick(prs, seen, reported, False)
        assert messages == ["PR #1 READY -> RED | Add feature"]
        assert live == {1: "RED"}
        assert reported == {1: ("RED", "def98765")}
