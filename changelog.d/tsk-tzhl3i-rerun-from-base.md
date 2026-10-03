### Fixed

- Moved the re-run helper for stale `bot-review-gate` runs into a separate `reconcile-stale-runs` job that checks out `github.event.pull_request.base.sha` instead of the PR merge ref, so the helper always executes from the base branch and cannot be replaced by a malicious PR. The `bot-review-gate` job no longer holds `actions: write`.
- Added `persist-credentials: false` to the `reconcile-stale-runs` checkout step so the workflow token is not written into the base tree's `.git/config`.
- Documented in `.claude/skills/taos-development-skill/SKILL.md` that the gate self-heals via `reconcile-stale-runs` and pointed the `actions: write` reference at the separate job and its base-SHA checkout.
- Restored `test_neither_trigger_is_dropped` in `tests/test_bot_review_gate_rerun.py` so a future PR that drops the `pull_request_review` trigger is still caught.
- Fixed `test_review_path_has_a_step_that_reruns_failed_same_sha_runs` to delegate to `_rerun_steps()` and assert the `--head-sha` / `PR_HEAD` wiring.
- Added RED-FIRST evidence to the test file showing the new `TestWorkflowStructure` cases fail against the pre-fix workflow and pass against the fixed workflow.
