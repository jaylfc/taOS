### Added

- `GET /api/device/v1/events` (scope `agents:read`, device bearer) emits
  Server-Sent Events of owner-filtered agent state changes: `agent.upsert`
  (name-keyed, full object including `avatar: {hue, hash}`), `agent.remove`,
  `decision.open`, `decision.close`, `agent.recap`, and a `snapshot` event
  when the client's `Last-Event-ID` is older than stream history. Heartbeat
  is a comment line `: ping` every 15 seconds.

### Security

- Revoking the device or losing the `agents:read` scope closes open SSE
  streams immediately (re-checked on every loop tick).
