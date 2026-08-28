### Fixed
- `scripts/check_bot_review.py` now matches both the workflow display name
  ("Bot review gate") and the job id ("bot-review-gate") when filtering
  check runs, so a stale `FAILURE` on either name is detected and reconciled
  instead of being skipped. `check_run_verdict` is now wired into `main()`
  so it is called on every terminal verdict instead of sitting uncalled.
