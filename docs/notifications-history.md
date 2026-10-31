# Notification history (session-only)

## GET /api/notifications/history

Searchable, filterable notification history with keyset paging.

Query parameters:
- `q` -- search `title` OR `message` (literal `%` and `_` via `ESCAPE '\'`)
- `source` -- repeatable, filter by source
- `level` -- repeatable, filter by level (`info`, `success`, `warning`, `error`)
- `since` -- epoch seconds, lower bound on `timestamp`
- `until` -- epoch seconds, upper bound on `timestamp`
- `state` -- `all` | `unread` | `read` | `archived` | `active` (default `all`)
- `before` -- opaque cursor `"<timestamp>:<id>"` for keyset paging (strictly older)
- `limit` -- page size 1..200 (default 50)

Returns: `{"items": [...], "next_before": "<cursor>" or null}`

Each item includes `archived` bool alongside the standard notification fields.

User scoping: own notifications + broadcasts (`user_id IS NULL`). Broadcast read/archive state is per-user via `notification_user_state`.

## GET /api/notifications/history/sources

Distinct notification sources visible to the calling user, sorted.

Returns: `{"sources": [...]}`

## GET /api/notifications/archived (legacy)

History view: dismissed notifications, newest first (nothing deleted). Returns a flat list (newest 50 only, no filters, no paging). The UI is migrating to `/history`.