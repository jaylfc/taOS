### Fixed

- `scripts/check_evil_merge.py` now allows a merge conflict resolution that matches one parent wholesale to pass the guard. Previously the guard compared the merge result only against the `git merge-tree` conflict-marker baseline, which incorrectly flagged legitimate conflict resolutions that took one side entirely.
- Updated the CLI failure output to say `matches neither parent` and to show only the merge and parent hashes, matching the documented gate contract.

### Added

- Added `tests/test_check_evil_merge.py::TestEvilMergeGuard::test_conflict_resolved_by_taking_one_side_wholesale_stays_green` covering the conflict-resolution control case.
