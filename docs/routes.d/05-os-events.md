# OS change-event stream (`GET /api/os/events`, session-only)

<!-- Route module `tinyagentos/routes/os_events.py`. SSE stream of typed OS-level change events, behind the session cookie -->

## SSE stream characteristics

- `?kinds=a,b,c` — comma-separated allowlist of event kinds
- Omitted/empty/no kind = every kind (empty allowlist = no filter, not silence)
- Filtering at buffer entry: unrequested kind never evicts requested
- Max 256 events/conn; oldest dropped → `{"kind": "events.lagged", "dropped": N}` cues refetch
- `:keepalive` comment every 10s prevents proxy close on idle
- No SSE `id:` line; resume via EventBus replay (last 32/channel, on subscribe)
- Payload never on wire: `id` is trace id, subscriber refetches to learn changes

## Desktop integration

- `desktop/src/hooks/use-os-events.ts`: `useOsEvents(kinds, onEvent)` holds one conn, returns `connected`/`stale`, dedupes by event id, reconnects with backoff, reopens on `kinds` change

## Technical details

- Subscriptions/relay tasks created INSIDE response generator, not handler: generator closed before iteration never runs `finally`; handler-side setup leaked sub per client disconnecting before stream start