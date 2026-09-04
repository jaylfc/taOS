### Added

- `useOsEvents` hook now shares a single EventSource across all callers in the same browser window, keeping one OS-level SSE connection per client instead of one per app.

### Changed

- The desktop client no longer sends `?kinds=` to `/api/os/events`. It opens one unfiltered stream and applies each subscriber's kind list in the browser, so widening a subscriber's kinds no longer reopens the connection. The server-side `?kinds=` filter is unchanged and still available to other clients.

### Fixed

- `useOsEvents` decides whether the shared stream stays open from the subscriber map alone, read once the React commit has settled. The previous per-component "am I still mounted?" guard could close and immediately reopen the stream when one subscriber unmounted in the same commit in which another changed its kinds or a replacement subscriber mounted.
- Connection status is published through a shared snapshot, so a stream that keeps erroring while the browser retries no longer re-renders every subscriber on each repeated error.
