### Added
- `GET /api/notifications/history` — searchable, filterable notification history with keyset paging (q, source, level, since, until, state, before, limit). Returns `{items, next_before}`. Server-side search with user scoping (own + broadcasts, per-user broadcast read/archive state).
- `GET /api/notifications/history/sources` — distinct notification sources visible to the user, sorted.
- `NotificationStore.search()` and `NotificationStore.history_sources()` backend methods.

### Fixed
- `NotificationStore.search()` with `state=read` now matches broadcasts the user marked read via `mark_read()` (stored as a `read_at` timestamp in `notification_user_state`), not only rows whose `read` column is exactly `1`.
- `GET /api/notifications/history` clamps `limit` to `1..200` before the store call and uses the clamped value for the `next_before` cursor, so a `limit=500` request returns a full 200-row page with a non-null cursor instead of silently stopping.