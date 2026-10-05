## Device API v1

The full spec now lives in `docs/device-api-v1.md`; routes under `/api/devices`
are bearer-authenticated. The following routes are public (no bearer token):

- `POST /api/devices/pair-requests`
- `GET /api/devices/pair-requests/{pair_request_id}`

### GET /api/device/v1/state

**Scope:** `agents:read` (device bearer).

Returns the owner-filtered agent list for the paired device, plus the server
version, current time, and demo flag.

**Response 200:**

All string fields are server-side capped with a trailing ellipsis when they
exceed the limit: `name` 48, `status` 120, `last_recap` 180, `question` 280,
each `option` 40.

```json
{
  "agents": [
    {
      "name": "alice-agent",
      "status": "running",
      "framework": "openclaw",
      "avatar": {
        "hue": 123,
        "hash": "abc123def4567890"
      },
      "attention": true,
      "last_recap": "recapped message...",
      "decision": {
        "id": "",
        "question": "Ship it?",
        "options": ["Approve", "Deny"]
      }
    }
  ],
  "server": {
    "version": "1.0.0-beta.55"
  },
  "time": 1727640000.123,
  "demo": false
}
```

**Error codes:**

- `401` -- missing or invalid device bearer.
- `403` -- FastAPI's wrapper: `{"detail": {"error": "device_scope_missing", "scope": "agents:read"}}` when the device token lacks the required scope.

### GET /api/device/v1/events

**Scope:** `agents:read` (device bearer).

Server-Sent Events stream of owner-filtered agent state changes for the
paired device. The stream emits one event per change and a heartbeat comment
every 15 seconds. Events are name-keyed: every event's data carries the
agent `name` key.

**Event names and data shapes:**

- `agent.upsert` -- an agent appeared or its object changed. Data is the
  full agent object (same shape as one `agents[]` entry of the state body,
  including `avatar: {hue, hash}`). Emitted on connect for every current
  agent so a fresh client can build state from typed events alone.
- `agent.remove` -- an agent disappeared. Data: `{"name": "<agent name>"}`.
- `decision.open` -- a pending decision appeared for an agent. Data:
  `{"name": "<agent name>", "decision": {...}}`.
- `decision.close` -- an agent's pending decision was resolved. Data:
  `{"name": "<agent name>"}`.
- `agent.recap` -- an agent's `last_recap` changed. Data:
  `{"name": "<agent name>", "last_recap": "<text>"}`.
- `snapshot` -- full state body, sent only when the client's `Last-Event-ID`
  is older than the stream's current history. Data is the same shape as the
  state endpoint response.

**Heartbeat:**

A comment line `: ping` is emitted every 15 seconds (configurable via
`_HEARTBEAT_INTERVAL_S`). Each heartbeat carries an increasing `id:` field.

**Resume and snapshot behaviour:**

The client may send `Last-Event-ID` to resume. If the ID is older than what
the stream still holds, the server sends one `snapshot` event and then
continues with typed events from the current state. Fresh connects (no
`Last-Event-ID` or `Last-Event-ID: 0`) start directly with typed
`agent.upsert` events and do not receive a snapshot.

**Close-on-revoke:**

The stream re-checks the device bearer token on every loop tick. If the
device is revoked or the scope is lost, the stream closes immediately.
