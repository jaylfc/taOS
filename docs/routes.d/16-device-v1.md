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

### GET /api/device/v1/agents/{name}/avatar

**Scope:** `agents:read` (device bearer). `size` query param is required and must
be `45` or `96`.

Returns the avatar converted server-side to LVGL 9 RGB565A8 (`Content-Type:
application/x-taos-lvimg`), so the device never decodes PNG/JPEG. Body: a 12-byte
little-endian `lv_image_header_t` (magic `0x19`, cf `0x14`, w, h, stride `w*2`),
then the RGB565 plane (little-endian) and the A8 alpha plane (circle mask):
`12 + w*h*3` bytes.

`ETag: "<avatar_hash>-<size>"`; a matching `If-None-Match` returns `304`.
`Cache-Control: private, max-age=86400`.

Server cache: `<data_dir>/cache/device-avatars/<agent_key>/<hash>-<size>.lvimg`,
written with `atomic_write_bytes`. The key is the content hash, so an avatar change
is a cache miss; stale entries for the agent are deleted on conversion.

**Error codes:**

- `400` -- `{"detail": {"error": "size_not_supported"}}`.
- `401` / `403` -- as for `/api/device/v1/state` (scope `agents:read`).
- `404` -- `{"detail": {"error": "avatar_not_found"}}` for a non-owned or unknown
  agent, no avatar, or an undecodable image. Never `403` (no name leak), never `500`.
