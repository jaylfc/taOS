### Fixed
- `scripts/check_evil_merge.py` now allows a merge conflict resolution that matches one parent wholesale to pass the guard **only when the path actually conflicted**. Previously the exemption was unconditional, which blinded the guard to evil merges where a clean auto-merge was resolved by taking one side wholesale and discarding the other parent's changes.
- Updated the CLI failure output to include the specific rule that fired (`matches neither parent` for conflicted paths, `clean merge not taken` for clean merges, `deleted file both parents kept` for silent drops) instead of the generic `matches neither parent`.

### Added
- Added `tests/test_check_evil_merge.py::TestEvilMergeGuard::test_conflict_resolved_by_taking_one_side_wholesale_stays_green` covering the conflict-resolution control case.
- Added `tests/test_check_evil_merge.py::TestEvilMergeGuard::test_clean_auto_merge_then_take_one_side_wholesale_is_violation` covering the clean-auto-merge-then-take-one-side case that must be flagged.