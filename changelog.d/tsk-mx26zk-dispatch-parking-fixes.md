### Fixed

- Remove unused `project_id` binding in `tests/projects/test_strike_wiring.py:142` (replace with `_` per RUF059)
- Make threshold parking indivisible from release: serialize strike recording and `park_task` in `release_task` so a concurrent claim between release and strike recording does not leave the task `parked` with a stale `claimed_by` value
- Prevent transitions out of `parked`: reject generic status transitions from `parked` in `update_task`, and add `parked` to the `NOT IN` list in `close_task` and `reopen_task` to prevent a parked task from being closed/reopened into the ready pool