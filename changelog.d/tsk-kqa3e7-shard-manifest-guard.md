### Added

- CI shard rebalance mechanism: `scripts/check_shard_manifest.py` guards the `tests/.test_durations` pytest-split duration manifest (validates coverage of collected ids and greedy least-duration shard balance), with unit tests in `tests/ci/`, and a `record-test-durations` manual workflow that records and validates the manifest. No `ci.yml` change in this PR; the manifest is committed in part 2.
