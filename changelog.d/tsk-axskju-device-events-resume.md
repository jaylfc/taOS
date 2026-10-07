### Fixed

- `GET /api/device/v1/events` now uses a per-owner shared event buffer so `Last-Event-ID` resumes from the next buffered event, reconnects are idempotent, and a `snapshot` is only sent when the id is outside the buffer. Heartbeat frames carry no `id:` line.
