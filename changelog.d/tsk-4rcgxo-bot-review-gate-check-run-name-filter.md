### Fixed
- `scripts/check_bot_review.py` now matches both the `Bot review gate` workflow
  display-name check run and the `bot-review-gate` job-id check run (the
  runner-owned run GitHub creates for the Actions job itself), so
  `list_check_runs` no longer drops the run that actually pins a self-healed PR
  on `mergeStateStatus: UNSTABLE` per the #2573 lead-block evidence.
  `check_run_verdict` is wired into `main()` as the head-SHA read side of the
  #2493 reconcile (it was previously defined but uncalled), and the suite gains
  `test_stale_failure_matched_by_job_id` (a `bot-review-gate`-named fixture that
  fails on the original line) plus a `Bot review gate`-named control so a fix
  that swaps one name for the other is caught rather than covering both.
