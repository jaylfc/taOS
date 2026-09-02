### Fixed

- `scripts/check_bot_review.py` no longer crashes with `UnboundLocalError` on
  every `--head-sha` run. The `RECONCILE_403_OCCURRED` module-level flag was
  both read and assigned inside `main()` without a `global` declaration, so
  the read at the verified-stub check raised before the reset assignment
  could run. The reconcile path now signals a 403-during-PATCH via the
  return value of `reconcile_head_sha_check_run` and the gate still keeps
  the verdict red when a stale FAILURE survives, instead of waiving it.