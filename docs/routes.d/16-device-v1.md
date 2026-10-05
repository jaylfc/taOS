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

**Scope:** `agents:read` (device bearer).

Returns the agent's avatar converted server-side to LVGL 9 native RGB565A8 format
(little-endian, transparent pixels = black). The device caches this by ETag
and never decodes PNG/JPEG.

**Query parameters:**

- `size` (required): whitelisted to `45` or `96`. Any other value returns `400`
  `{"detail": {"error": "size_not_supported"}}`.

**Response 200:**

Content-Type: `application/x-taos-lvimg`

Body: LVGL 9 compatible image (12-byte header + RGB565 plane + A8 alpha plane):

```
Header (12 bytes, little-endian, compatible with lv_image_header_t):
  uint8_t  magic        = 0x19
  uint8_t  cf           = 0x14 (LV_COLOR_FORMAT_RGB565A8)
  uint8_t  flags        = 0
  uint8_t  reserved0    = 0
  uint16_t w            = size
  uint16_t h            = size
  uint16_t stride       = w * 2  (RGB565 stride in bytes)
  uint16_t reserved1    = 0

RGB565 plane: stride * h bytes (native little-endian: pure red = 0xF800 stored as 00 F8)
A8 alpha plane: w * h bytes (anti-aliased circle mask, 0 = transparent, 255 = opaque)

Total body size: 12 + w*h*3 bytes.
```

**ETag / 304:**

`ETag: "<avatar_hash>-<size>"` (quoted, e.g. `"abc123def4567890-96"`).

If `If-None-Match` matches the ETag, returns `304 Not Modified` with empty body.
`Cache-Control: private, max-age=86400`.

**Server-side cache:**

Converted images are cached under `<data_dir>/cache/device-avatars/<agent_key>/<hash>-<size>.lvimg`
(atomic write via tmp + rename). Key is the content hash, so an avatar change is
automatically a cache miss. Stale entries for the same agent (different sizes or old
hashes) are deleted on conversion.

**Error codes:**

- `400` -- `size` not in whitelist: `{"detail": {"error": "size_not_supported"}}`.
- `401` -- missing or invalid device bearer.
- `403` -- device token lacks `agents:read` scope: `{"detail": {"error": "device_scope_missing", "scope": "agents:read"}}`.
- `404` -- agent not owned by device user, no avatar installed, or source image
  undecodable: `{"detail": {"error": "avatar_not_found"}}`. Never `403` for
  unknown/non-owned names (no name leak). Never `500` for bad source images.
