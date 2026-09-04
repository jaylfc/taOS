### Fixed
- Wake-budget enforcement test now closes the debounced task and creates a fresh ready task before the third tick, so the tick actually reaches `can_wake` and verifies that budget exhaustion blocks further wakes instead of silently skipping at debounce.
- `_read_state` now validates the nested `daily` shape (`daily` and each per-key value must be JSON objects), so a well-formed file with a wrong nested shape degrades the fleet row as `damaged` instead of raising `AttributeError` out of `get_fleet_wake_info`.
