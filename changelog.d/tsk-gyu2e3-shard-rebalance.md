### Fixed

- Shard CI by measured test runtime using greedy longest-first bin-packing
  over a checked-in timing manifest (`tests/.test_durations`) instead of
  alphabetical file slicing. The recorded green run had a 4.5x spread
  (4 min fastest vs 18.1 min slowest); the new split targets <= 2x.
- Added `tests/ci/test_shard_balance.py` which fails CI if any shard is
  more than 2x the runtime of the fastest shard, preventing silent rot
  as tests are added.
