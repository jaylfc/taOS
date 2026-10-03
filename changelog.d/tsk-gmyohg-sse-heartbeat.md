### Fixed
- SSE OS events stream now emits a `events.heartbeat` data frame alongside the
  proxy keepalive comment, and the desktop hook arms a 25 s watchdog that
  detects a half-open connection the native EventSource would otherwise never
  notice, flipping the stream to stale and reconnecting through the backoff.
