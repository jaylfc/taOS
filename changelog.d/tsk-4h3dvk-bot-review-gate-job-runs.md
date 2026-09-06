### Fixed
- `scripts/check_bot_review.py` now skips runner-owned GitHub Actions job check
  runs (those with a non-null `external_id` or a `details_url` under
  `/actions/runs/`) during reconcile and verdict reads. The Actions API token
  cannot PATCH these runs, so a stale job-owned FAILURE previously caused
  reconcile to fail closed and pin every subsequent gate run red. Job-owned
  staleness is irrelevant because GitHub's merge box keys off the latest check
  run per name, and only the script's own runs are writable.