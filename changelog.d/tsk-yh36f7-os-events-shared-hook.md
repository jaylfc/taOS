### Added

- `useOsEvents` hook now shares a single EventSource across all callers in the same browser window, keeping one OS-level SSE connection per client instead of one per app.

### Changed

- The desktop client opens `/api/os/events` with `?kinds=` set to the union of every live subscriber's kind list, then applies each subscriber's own list in the browser. Widening into kinds the union already covers no longer reopens the connection, and the union is never narrowed when a subscriber leaves, so it settles at one reopen per distinct kind for as long as at least one subscriber stays mounted (the union resets with the connection when the last one leaves). A reopen overlaps the old and new streams and closes the old one only once the new one is live, so no events are lost across a filter change.

### Fixed

- `useOsEvents` decides whether the shared stream stays open from the subscriber map alone, read once the React commit has settled. The previous per-component "am I still mounted?" guard could close and immediately reopen the stream when one subscriber unmounted in the same commit in which another changed its kinds or a replacement subscriber mounted.
- Connection status is published through a shared snapshot, so a stream that keeps erroring while the browser retries no longer re-renders every subscriber on each repeated error.
- `useOsEvents` keeps the 128-id dedup window per subscriber rather than once for the shared stream, so a busy event kind can no longer evict a quiet subscriber's ids and make it handle a replayed event twice.
- A subscriber mounting while a reconnect is already scheduled no longer cancels it, so a view that mounts callers repeatedly against a down endpoint retries on the 5s-to-30s backoff instead of at mount frequency.
- Each subscriber's handler runs inside its own isolation boundary on the shared stream, so an app whose handler throws can no longer stop later subscribers from receiving that OS event (`events.lagged` included). The failure is logged and delivery continues.
