# OS change-event stream (`GET /api/os/events`, session-only)

<!-- Route module `tinyagentos/routes/os_events.py`. SSE stream of typed OS-level change events, behind the session cookie -->

## SSE stream characteristics

- `?kinds=a,b,c` — comma-separated allowlist of event kinds
- Omitted, empty, or naming no kind at all (`?kinds=`, `?kinds=%20`, `?kinds=,`) means every kind (empty allowlist = no filter, not silence)
- Filtering happens as events enter the per-connection buffer, so an unrequested kind can never evict one the subscriber asked for
- At most 256 events are buffered per connection; past that the OLDEST is dropped and the client gets `{"kind": "events.lagged", "dropped": N}` as a cue to refetch
- A `:keepalive` comment frame every 10 s keeps proxies from closing an idle stream
- Frames carry **no** SSE `id:` line; resume is best-effort via the EventBus replay buffer (last 32 events per channel, delivered on subscribe)
- The payload never crosses the wire: `id` is just the trace id, so a subscriber refetches to learn what changed

## Desktop integration

- `desktop/src/hooks/use-os-events.ts`: `useOsEvents(kinds, onEvent)` holds one connection, returns `connected` / `stale`, dedupes by event id, reconnects with backoff, and reopens the stream when `kinds` changes

## Technical details

- Subscriptions and relay tasks are created INSIDE the response generator, not the handler body: a generator closed before iteration never runs its `finally`; handler-side setup leaked a subscription per client that disconnected before the stream started
