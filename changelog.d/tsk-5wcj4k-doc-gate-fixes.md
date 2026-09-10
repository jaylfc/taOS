### Fixed

- Non-ASCII paths in changed files now correctly trigger doc-gate rules: `git -c core.quotePath=false diff -z` is used unconditionally in all four gate scripts, and the copy-pasted `_run_git` / `_parse_name_status` helpers are extracted into `scripts/_gitutil.py`.
- Mid-pattern `**` globs such as `docs/**/*.md` and `a/**/b` now match correctly: the hand-written `_glob_match` is replaced with `pathspec` (MPL-2.0, CI-only, no runtime deps).
- The store-wiring check no longer satisfied by a comment or string literal: `_class_def_in_added_lines` now uses `unidiff` (MIT) to inspect only added lines within each hunk rather than running a regex over raw diff text.
