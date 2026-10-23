### Added

- **CI shard manifest guard**: add scripts/check_shard_manifest.py to validate tests/.test_durations against collected tests and enforce balanced sharding; add tests/ci/test_shard_balance.py and a manual record-test-durations workflow.

### Fixed

- **Shard validator collection order**: scripts/check_shard_manifest.py now preserves pytest's collection order (not hash order) when splitting shards; collected_ids() fails on collection errors instead of silently accepting partial results; record-test-durations.yml uses shell: bash for pipefail.
