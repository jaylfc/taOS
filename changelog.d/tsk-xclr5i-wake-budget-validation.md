### Fixed

- `validate_config()` now rejects a non-mapping `wake_budget` (e.g. `[1]` or `[]`) instead of crashing with `AttributeError` or silently treating it as an empty config.
- `validate_config()` now requires `wake_budget.global_default` and per-agent/per-project values to be real `int` instances, explicitly rejecting floats (which `int()` truncates) and booleans. A one-character config typo such as `0.9` no longer silently disables all scheduled wakes fleet-wide.
- `get_fleet_wake_info` now catches `WakeBudgetStateError` per agent and degrades only the affected row (null `next_wake_epoch`, consumed:0, remaining:0, explicit `state: "damaged"` marker) instead of letting the exception propagate and take out the whole fleet report when `wake_budget.json` is damaged.
