### Added

- taOS Agent audit rows: the action gate records every request and every outcome (executed, declined, failed) as an `external_write` trace row on the `taos-agent` trace, a new `local_fix` kind is ready for reversible fixes, the trace store refuses either kind with a missing field (for example `confirmed_by`), `POST /api/trace` refuses both, the agent trace list gains a kind filter, and the taOS agent panel gains an Activity tab (when, what, where, Open link).
- The trace list returns `external_write` and `local_fix` rows only to the user whose `user_id` they carry.
