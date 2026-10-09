### Fixed

- `GET /api/device/v1/events` now uses a per-owner shared event buffer so `Last-Event-ID` resumes from the next buffered event, reconnects are idempotent, and a `snapshot` is only sent when the id is outside the buffer. Heartbeat frames carry no `id:` line.
- Multi-agent diff tail in `_events_stream` is no longer nested inside the per-agent loop, so `agent.upsert`, `agent.recap`, and `decision.*` events now fire for every agent on each poll, and `agent.remove` fires correctly when the agent list becomes empty.
- Resume after disconnect delivers agent changes (e.g., avatar updates) made while the client was offline by tracking the last emitted agent state per owner.
- The events stream re-checks the device before every frame it sends ahead of the poll loop, and event ids are allocated and recorded atomically.
- A resumed events stream takes its replay and its baseline in one locked read, so a change recorded by another stream mid-replay is still delivered, and every decision.close carries decision_id.
