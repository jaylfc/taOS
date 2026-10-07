## Device API v1

The full spec lives in `docs/device-api-v1.md`; routes under `/api/devices`
are bearer-authenticated. Public routes: `POST /api/devices/pair-requests`,
`GET /api/devices/pair-requests/{pair_request_id}`.

### GET /api/device/v1/state

**Scope:** `agents:read` (device bearer).

Returns the owner-filtered agent list plus server version, time, and demo flag.
String fields are capped: `name` 48, `status` 120, `last_recap` 180,
`question` 280, each `option` 40.

**Errors:** `401` missing/invalid bearer, `403` scope missing.

### GET /api/device/v1/events

**Scope:** `agents:read` (device bearer).

SSE stream of owner-filtered agent state changes. Emits one event per change
and a heartbeat comment every 15 seconds. Events are name-keyed.

**Events:**

- `agent.upsert` -- agent appeared or changed. Data: full agent object
  (same shape as one `agents[]` entry of the state body).
- `agent.remove` -- agent disappeared. Data: `{"name": "<name>"}`.
- `decision.close` -- decision resolved. Data:
  `{"name": "<name>", "decision_id": "<old id>"}`.
- `decision.open` -- decision appeared. Data:
  `{"name": "<name>", "decision": {...}}`.
- `agent.recap` -- recap changed. Data:
  `{"name": "<name>", "last_recap": "<text>"}`.
- `snapshot` -- full state body, sent when `Last-Event-ID` is outside the
  server's buffer.

**Heartbeat:**

`: ping` every 15 seconds. Heartbeat frames carry no `id:` line.

**Resume:**

The client may send `Last-Event-ID` to resume. If the ID is inside the
server's per-owner event buffer, replay only events with a higher id and
do not send a snapshot. If the ID is older than the oldest buffered event,
newer than the newest, or the owner has no buffer yet, send one `snapshot`
first. Fresh connects (no `Last-Event-ID` or `Last-Event-ID: 0`) start with
typed `agent.upsert` events.

**Close-on-revoke:**

The stream re-checks the device bearer token on every loop tick. If the
device is revoked, blocked or the scope is lost (specifically `agents:read`),
the stream closes immediately.

### GET /api/device/v1/agents/{name}/avatar

**Scope:** `agents:read` (device bearer). `size` (required): `45` or `96`.

LVGL 9 RGB565A8 avatar (`application/x-taos-lvimg`): a 12-byte
`lv_image_header_t` (magic `0x19`, cf `0x14`), then RGB565 and A8 (circle mask)
planes, `12 + w*h*3` bytes. `ETag: "<avatar_hash>-<size>"`; a matching
`If-None-Match` returns `304`.

**Errors:** `400` `size_not_supported`; `401`/`403` as for state; `404`
`avatar_not_found` (non-owned, unknown, no avatar, undecodable; never
`403` or `500`).
