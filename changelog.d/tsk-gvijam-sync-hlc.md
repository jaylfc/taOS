### Added

- New `tinyagentos/sync/hlc.py` hybrid logical clock library (Kulkarni et al.) with a `Timestamp` dataclass, `node_id`, `pack`/`unpack`, and an `HLC` with `now()` and `recv()`. Pure, stdlib only; nothing uses it yet.