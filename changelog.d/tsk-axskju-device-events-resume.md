### Fixed

- `GET /api/device/v1/events` now uses a per-owner shared event buffer so `Last-Event-ID` resumes from the next buffered event, reconnects are idempotent, and a `snapshot` is only sent when the id is outside the buffer. Heartbeat frames carry no `id:` line.
- Multi-agent diff tail in `_events_stream` is no longer nested inside the per-agent loop, so `agent.upsert`, `agent.recap`, and `decision.*` events now fire for every agent on each poll, and `agent.remove` fires correctly when the agent list becomes empty.
