### Added

- New `tinyagentos/sync/hlc.py` hybrid logical clock library (Kulkarni et al.) with a `Timestamp` dataclass, `node_id`, `pack`/`unpack`, and an `HLC` with `now()` and `recv()`. Pure, stdlib only; nothing uses it yet.

### Fixed

- `HLC.__init__` default wall clock is now a callable re-evaluated on each `now()`/`recv()` call instead of a single `int(time.time() * 1000)` captured at construction, so the clock no longer returns a frozen wall time.