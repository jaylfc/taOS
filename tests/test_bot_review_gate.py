"""Acceptance tests for the bot-review-gate check-run filter bug.

GitHub emits two separate check runs for the same workflow on a SHA: the
workflow DISPLAY NAME ("Bot review gate") and the JOB ID ("bot-review-gate").
mergeStateStatus keys off ANY failing check run, so the filter in
list_check_runs() must catch BOTH. A filter on either name alone skips the
other, leaving a stale FAILURE to pin the PR on UNSTABLE.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check_bot_review.py"


def _load_module():
    import importlib.util
    import sys
    spec = importlib.util.spec_from_file_location("check_bot_review", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_bot_review"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def check_mod():
    return _load_module()


class TestBotReviewGateFilter:
    """The filter must catch both the display-name run and the job-id run."""

    HEAD_SHA = "1baa21a" + "0" * 34

    @staticmethod
    def _run(run_id, conclusion, started_at, /, *, in_progress=False, name="Bot review gate"):
        return {
            "id": run_id,
            "name": name,
            "head_sha": "1baa21a",
            "status": "in_progress" if in_progress else "completed",
            "conclusion": None if in_progress else conclusion,
            "started_at": started_at,
            "completed_at": started_at,
        }

    @staticmethod
    def _mock_list(check_runs):
        def side_effect(url, token=None):
            return [{"total_count": len(check_runs), "check_runs": check_runs}]
        return side_effect

    def test_stale_failure_matched_by_job_id(self, check_mod) -> None:
        """A run named 'bot-review-gate' (the job id) must be matched by the
        filter. On the buggy code this is skipped, so the verdict reads green
        instead of red."""
        runs = [
            self._run(32071499652, "failure", "2026-08-17T21:30:58Z", name="bot-review-gate"),
        ]
        with patch.object(check_mod, "_api_get", side_effect=self._mock_list(runs)):
            exit_code, message = check_mod.check_run_verdict("jaylfc", "taOS", self.HEAD_SHA)
        assert exit_code == 1
        assert "failure" in message

    def test_stale_failure_matched_by_display_name(self, check_mod) -> None:
        """Control: a run named 'Bot review gate' (the display name) must still
        be matched. This catches a fix that swaps one name for the other."""
        runs = [
            self._run(32071499652, "failure", "2026-08-17T21:30:58Z", name="Bot review gate"),
        ]
        with patch.object(check_mod, "_api_get", side_effect=self._mock_list(runs)):
            exit_code, message = check_mod.check_run_verdict("jaylfc", "taOS", self.HEAD_SHA)
        assert exit_code == 1
        assert "failure" in message

    def test_foreign_check_run_is_ignored(self, check_mod) -> None:
        """A check run with neither name must not decide the verdict."""
        runs = [
            {"id": 9, "name": "CodeRabbit", "status": "completed", "conclusion": "success",
             "started_at": "2026-08-28T00:00:00Z", "completed_at": "2026-08-28T00:00:00Z"},
            self._run(32071499652, "failure", "2026-08-17T21:30:58Z", name="bot-review-gate"),
        ]
        with patch.object(check_mod, "_api_get", side_effect=self._mock_list(runs)):
            exit_code, message = check_mod.check_run_verdict("jaylfc", "taOS", self.HEAD_SHA)
        assert exit_code == 1
        assert "failure" in message
