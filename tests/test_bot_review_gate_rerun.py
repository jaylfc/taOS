"""Tests for the bot-review-gate stale-run re-run (tsk-dzd77m).

Defect: `.github/workflows/bot-review-gate.yml` fires on BOTH
`pull_request` and `pull_request_review`, and each event creates a SEPARATE
workflow run / check suite on the same head SHA. The `pull_request` run fires
on open, BEFORE CodeRabbit has reviewed, and fails; the later
`pull_request_review` run passes. GitHub's required-check evaluation keeps
BOTH check runs named `bot-review-gate`, so the rollup stays FAILURE and
mergeStateStatus stays BLOCKED forever -- `gh pr merge --auto` never fires.

Fix: when the `pull_request_review` run of the gate PASSES, re-run the failed
`pull_request` runs of THIS workflow for the same head SHA. The re-run
re-reads review state from the API at run time, so it reaches the same
verdict as the run that just passed.

# RED-FIRST evidence (run against base-branch workflow, then fixed workflow):
#
# === RED (base branch exec/tsk-dzd77m) ===
# FAILED tests/test_bot_review_gate_rerun.py::TestWorkflowStructure::test_gate_job_has_no_actions_permission
# FAILED tests/test_bot_review_gate_rerun.py::TestWorkflowStructure::test_rerun_job_checks_out_base_sha_and_has_actions_write
# FAILED tests/test_bot_review_gate_rerun.py::TestWorkflowStructure::test_rerun_job_is_gated_to_pull_request_review_and_needs_gate
# ======================================
#
# === GREEN (this branch head) ===
# 18 passed in 0.53s
# ================================

Measured on #3394 (runs 37080299756 pull_request/failure,
37080574868 pull_request_review/success) and #3400.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
_HELPER = REPO_ROOT / "scripts" / "rerun_failed_bot_review_runs.py"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "bot-review-gate.yml"

HEAD_SHA = "086d66476" + "0" * 31
OTHER_SHA = "e7e93b2af" + "0" * 31


def _load_helper():
    spec = importlib.util.spec_from_file_location("rerun_failed_bot_review_runs", _HELPER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["rerun_failed_bot_review_runs"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def helper():
    return _load_helper()


def _invocations(step):
    """Non-comment command lines of a `run:` block.

    These blocks are mostly prose, so a substring match over the whole block
    also matches the explanation and stays green when the command loses its
    arguments -- the exact defect the workflow assertions guard.
    """
    return [
        ln.strip() for ln in step.get("run", "").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


def _run(run_id, *, name, head_sha, conclusion, event, status="completed"):
    """One entry of the Actions `workflow_runs` list."""
    return {
        "id": run_id,
        "name": name,
        "head_sha": head_sha,
        "conclusion": conclusion,
        "event": event,
        "status": status,
    }


# The measured #3394 rollup: run 37080299756 (event pull_request, failure)
# coexisting with run 37080574868 (event pull_request_review, success) on the
# SAME head SHA. Plus every neighbour the selection must NOT touch.
PAYLOAD_RUNS = [
    # the stale failure that pins the PR on BLOCKED -- must be selected
    _run(37080299756, name="Bot review gate", head_sha=HEAD_SHA,
         conclusion="failure", event="pull_request"),
    # the pull_request_review run doing the re-running -- must never re-run itself
    _run(37080574868, name="Bot review gate", head_sha=HEAD_SHA,
         conclusion="success", event="pull_request_review"),
    # same workflow, already green -- nothing to fix
    _run(37080299000, name="Bot review gate", head_sha=HEAD_SHA,
         conclusion="success", event="pull_request"),
    # same workflow, still in flight -- not a verdict, and it is this run
    _run(37080299111, name="Bot review gate", head_sha=HEAD_SHA,
         conclusion=None, event="pull_request_review", status="in_progress"),
    # a DIFFERENT workflow's failure on the same SHA -- not ours to touch
    _run(37080299222, name="Changelog fragment gate", head_sha=HEAD_SHA,
         conclusion="failure", event="pull_request"),
    # a failure on a DIFFERENT head SHA -- must not be re-run under this PR
    _run(37080299333, name="Bot review gate", head_sha=OTHER_SHA,
         conclusion="failure", event="pull_request"),
    # a failed review-event run of this workflow -- the re-run must target the
    # pull_request event only, never re-trigger itself in a loop
    _run(37080299444, name="Bot review gate", head_sha=HEAD_SHA,
         conclusion="failure", event="pull_request_review"),
]


class TestRunSelection:
    """The helper must select ONLY this workflow's failed same-SHA pull_request
    runs. A wider selection re-runs other gates' failures (noise, and turns a
    green PR red for an unrelated reason) or re-triggers this gate in a loop.
    """

    def test_selects_only_failed_same_sha_pull_request_runs_of_this_workflow(self, helper) -> None:
        selected = helper.select_failed_runs(PAYLOAD_RUNS, HEAD_SHA)
        assert [r["id"] for r in selected] == [37080299756], (
            "selection must be exactly the failed pull_request run of this "
            "workflow on the same head SHA; got "
            f"{[r['id'] for r in selected]}"
        )

    def test_drops_a_failure_on_another_head_sha(self, helper) -> None:
        selected = helper.select_failed_runs(PAYLOAD_RUNS, HEAD_SHA)
        assert OTHER_SHA not in {r["head_sha"] for r in selected}

    def test_drops_other_workflows_failures(self, helper) -> None:
        selected = helper.select_failed_runs(PAYLOAD_RUNS, HEAD_SHA)
        assert all(r["name"] == "Bot review gate" for r in selected)

    def test_drops_non_failure_conclusions(self, helper) -> None:
        selected = helper.select_failed_runs(PAYLOAD_RUNS, HEAD_SHA)
        assert all(r["conclusion"] == "failure" for r in selected)

    def test_drops_the_review_run_being_executed(self, helper) -> None:
        # Re-running the pull_request_review run that is doing the re-running
        # is a self-trigger loop; the selection is pull_request-event only.
        selected = helper.select_failed_runs(PAYLOAD_RUNS, HEAD_SHA)
        assert all(r["event"] == "pull_request" for r in selected)

    def test_empty_when_nothing_failed(self, helper) -> None:
        assert helper.select_failed_runs([], HEAD_SHA) == []

    def test_payload_extraction_reads_workflow_runs_key(self, helper) -> None:
        """GET /actions/runs wraps the list in {"workflow_runs": [...]}; a
        selection that reads the wrong key sees nothing and the helper reports
        'nothing to re-run' while the stale FAILURE survives."""
        payload = {"total_count": len(PAYLOAD_RUNS), "workflow_runs": PAYLOAD_RUNS}
        assert helper.extract_workflow_runs(payload) == PAYLOAD_RUNS


class TestRerunFailedJobs:
    """Each selected run is re-run with rerun-failed-jobs, and only those."""

    def test_main_reruns_failed_jobs_of_selected_runs_only(self, helper) -> None:
        payload = {"total_count": len(PAYLOAD_RUNS), "workflow_runs": PAYLOAD_RUNS}
        with patch.object(helper, "_api_get", return_value=payload) as get, \
                patch.object(helper, "_api_mutate", return_value=True) as mutate:
            rc = helper.main([
                "--head-sha", HEAD_SHA, "--owner", "jaylfc", "--repo", "taOS",
            ])
        assert rc == 0

        get_url = get.call_args[0][0]
        assert f"head_sha={HEAD_SHA}" in get_url, get_url
        assert "event=pull_request" in get_url, get_url

        posted = [call.args[0] for call in mutate.call_args_list]
        assert posted == [
            "https://api.github.com/repos/jaylfc/taOS/actions/runs/"
            "37080299756/rerun-failed-jobs",
        ], posted

    def test_main_is_best_effort_so_the_gate_stays_green(self, helper) -> None:
        """A failed re-run POST must NOT turn the gate's own check red: the
        gate run that is doing the healing is the one branch protection
        evaluates, and failing it recreates the BLOCKED condition this fix
        exists to remove."""
        payload = {"total_count": 1, "workflow_runs": PAYLOAD_RUNS[:1]}
        with patch.object(helper, "_api_get", return_value=payload), \
                patch.object(helper, "_api_mutate", return_value=False):
            rc = helper.main([
                "--head-sha", HEAD_SHA, "--owner", "jaylfc", "--repo", "taOS",
            ])
        assert rc == 0

    def test_main_exits_zero_when_listing_fails(self, helper) -> None:
        with patch.object(helper, "_api_get", return_value=None), \
                patch.object(helper, "_api_mutate", return_value=True) as mutate:
            rc = helper.main([
                "--head-sha", HEAD_SHA, "--owner", "jaylfc", "--repo", "taOS",
            ])
        assert rc == 0
        assert mutate.call_count == 0

    def test_main_requires_a_head_sha(self, helper) -> None:
        with patch.object(helper, "_api_get", return_value={"workflow_runs": []}) as get:
            rc = helper.main(["--owner", "jaylfc", "--repo", "taOS"])
        assert rc != 0
        assert get.call_count == 0


class TestWorkflowWiring:
    """The committed workflow YAML must actually do it. A helper with no
    caller is dead code and the PR would be BLOCKED exactly as before while the
    suite reported success.
    """

    @staticmethod
    def _rerun_steps():
        text = WORKFLOW.read_text(encoding="utf-8")
        assert "bot-review-gate" in text, "workflow YAML did not load"
        spec = yaml.safe_load(text)
        return spec["jobs"]["reconcile-stale-runs"]["steps"]

    @staticmethod
    def _gate_steps():
        text = WORKFLOW.read_text(encoding="utf-8")
        spec = yaml.safe_load(text)
        return spec["jobs"]["bot-review-gate"]["steps"]

    def test_review_path_has_a_step_that_reruns_failed_same_sha_runs(self) -> None:
        steps = self._rerun_steps()
        reruns = [s for s in steps if "rerun_failed_bot_review_runs.py" in s.get("run", "")]
        assert len(reruns) == 1, (
            "bot-review-gate.yml has no step invoking "
            f"scripts/rerun_failed_bot_review_runs.py; found {len(reruns)}"
        )
        step = reruns[0]
        invocations = [
            ln for ln in _invocations(step)
            if "rerun_failed_bot_review_runs.py" in ln
        ]
        assert len(invocations) == 1, invocations
        assert "--head-sha" in invocations[0], invocations[0]
        assert "PR_HEAD" in step.get("env", {}), (
            "the re-run step passes --head-sha but does not bind PR_HEAD; the "
            "flag expands to empty and nothing is selected"
        )

    def test_rerun_step_passes_the_head_sha(self) -> None:
        steps = self._rerun_steps()
        step = [
            s for s in steps if "rerun_failed_bot_review_runs.py" in s.get("run", "")
        ][0]
        invocations = [
            ln for ln in _invocations(step)
            if "rerun_failed_bot_review_runs.py" in ln
        ]
        assert len(invocations) == 1, invocations
        assert "--head-sha" in invocations[0], invocations[0]
        assert "PR_HEAD" in step.get("env", {}), (
            "the re-run step passes --head-sha but does not bind PR_HEAD; the "
            "flag expands to empty and nothing is selected"
        )

    def test_gate_job_grants_actions_write(self) -> None:
        spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        perms = spec["jobs"]["reconcile-stale-runs"]["permissions"]
        assert perms.get("actions") == "write", (
            "the reconcile-stale-runs job needs actions: write to re-run its "
            f"own failed runs; got {perms}"
        )

    def test_neither_trigger_is_dropped(self) -> None:
        """Both events must stay: the pull_request run is the fast red the gate
        exists for, the pull_request_review run is the one that can pass."""
        spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        trigger = spec.get("on", spec.get(True))
        assert "pull_request" in trigger
        assert "pull_request_review" in trigger

    def test_gate_check_still_passes_head_sha(self) -> None:
        """Regression guard: this PR must not touch the --head-sha wiring on
        the check step itself."""
        steps = self._gate_steps()
        gate = [
            s for s in steps
            if any("check_bot_review.py" in ln for ln in _invocations(s))
        ]
        assert len(gate) == 1
        invocations = [
            ln for ln in _invocations(gate[0]) if "check_bot_review.py" in ln
        ]
        assert "--head-sha" in invocations[0]


class TestWorkflowStructure:
    """The re-run helper must run from the base ref in its own job, not from the
    PR checkout inside bot-review-gate. A helper executed from the PR tree with
    actions: write lets any PR replace the script and harvest a token that can
    cancel, re-run or dispatch workflows."""

    def test_gate_job_has_no_actions_permission(self) -> None:
        spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        perms = spec["jobs"]["bot-review-gate"]["permissions"]
        assert "actions" not in perms, (
            "bot-review-gate must not hold actions: write so a malicious PR "
            f"cannot abuse the token; got {perms}"
        )

    def test_rerun_job_checks_out_base_sha_and_has_actions_write(self) -> None:
        spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        jobs = spec["jobs"]
        rerun_job = jobs.get("reconcile-stale-runs")
        assert rerun_job is not None, (
            "expected a separate reconcile-stale-runs job; "
            f"found jobs: {list(jobs)}"
        )
        perms = rerun_job.get("permissions", {})
        assert perms.get("actions") == "write", (
            "reconcile-stale-runs needs actions: write to re-run failed "
            f"workflow runs; got {perms}"
        )
        checkout = [
            s for s in rerun_job.get("steps", [])
            if s.get("uses", "").startswith("actions/checkout")
        ]
        assert len(checkout) == 1, checkout
        ref = checkout[0].get("with", {}).get("ref", "")
        assert ref == "${{ github.event.pull_request.base.sha }}", (
            "reconcile-stale-runs must check out the base ref so the helper "
            f"always comes from the base branch; got ref={ref!r}"
        )

    def test_rerun_job_is_gated_to_pull_request_review_and_needs_gate(self) -> None:
        spec = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        jobs = spec["jobs"]
        rerun_job = jobs.get("reconcile-stale-runs")
        assert rerun_job is not None, (
            "expected a separate reconcile-stale-runs job; "
            f"found jobs: {list(jobs)}"
        )
        assert rerun_job.get("if") == (
            "github.event_name == 'pull_request_review'"
        ), (
            "reconcile-stale-runs must only run on pull_request_review so it "
            "does not re-trigger itself; "
            f"got if: {rerun_job.get('if')!r}"
        )
        assert rerun_job.get("needs") == "bot-review-gate", (
            "reconcile-stale-runs must depend on bot-review-gate so it only "
            f"runs after the gate has passed; got needs: {rerun_job.get('needs')!r}"
        )
