### Fixed

- `check_doc_gate.py` diff-gate clean-run path now exits 0 when the apps-added rule
  is configured: the `_existing_toplevel_app_dirs` lookup is mocked in
  `test_clean_run_still_exits_0` and `test_genuine_violation_still_exits_1` so CI
  shards no longer see a `StopIteration`. New tests pin that a file added inside an
  existing desktop app directory does not trip the rule, while a brand-new app
  directory still does.
