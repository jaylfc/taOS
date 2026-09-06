### Fixed
- `scripts/check_bot_review.py` now anchors its "Bot review gate" check run
  verdict to the PR head SHA so a later `SUCCESS` supersedes an earlier
  `FAILURE` on the same SHA (#2493). Previously the gate published a fresh
  check run for every workflow run but never reconciled the old one, so a
  self-heal left a stale `FAILURE` coexisting with the new `SUCCESS` and
  `mergeStateStatus` stayed `UNSTABLE` forever. `check_run_verdict` reads the
  latest *completed* bot-review-gate run as authoritative (in-progress runs
  are skipped so a half-written verdict never clears a stale red), and
  `reconcile_head_sha_check_run` PATCHes stale runs to the new conclusion and
  POSTs a fresh run when absent. The gate also splits its single
  `CODERABBIT_SCAFFOLDING_RE` into per-fragment `is_coderabbit_acknowledgement`
  and `is_coderabbit_auto_summary` detectors so a regression in one cannot be
  masked by the other, and the main job now passes `--head-sha` with
  `checks: write` so every relevant PR event re-anchors the verdict.
