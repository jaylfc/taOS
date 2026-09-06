# OS change-event stream (`GET /api/os/events`, session-only)

<!-- Route module `tinyagentos/routes/os_events.py`. SSE stream of typed OS-level change events, behind the session cookie -->

## SSE stream characteristics

- `?kinds=a,b,c` — comma-separated allowlist of event kinds
- Omitted, empty, or naming no kind at all (`?kinds=`, `?kinds=%20`, `?kinds=,`) means every kind: an empty allowlist is "no filter", so a blank parameter can no longer build a set that matches nothing and deliver silence
- Filtering happens as events enter the per-connection buffer, so an unrequested kind can never evict one the subscriber asked for
- At most 256 events are buffered per connection; past that the OLDEST is dropped and the client gets `{"kind": "events.lagged", "dropped": N}` — its cue to refetch rather than assume it saw everything
- A `:keepalive` comment frame every 10 s keeps proxies from closing an idle stream
- Frames deliberately carry **no** SSE `id:` line (that is what makes a browser send `Last-Event-ID`, which this endpoint ignores): resume is best-effort through the EventBus replay buffer (last 32 events per channel, delivered on subscribe)
- The payload never crosses the wire: `id` is the event's trace id; a subscriber learns that something changed and refetches to learn what

## Desktop integration

- `desktop/src/hooks/use-os-events.ts`: `useOsEvents(kinds, onEvent)` holds one connection, returns `connected` / `stale`, dedupes by event id, reconnects with exponential backoff, and reopens the stream when `kinds` changes (the URL is fixed per connection)

## Technical details

- Subscriptions and relay tasks are created INSIDE the response generator, not the handler body: a generator closed before iteration never runs, so its `finally` can only undo setup done there; handler-side setup leaked a subscription per client that disconnected before the stream started