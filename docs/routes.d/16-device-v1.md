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
- `403` -- `{"error": "device_scope_missing", "scope": "agents:read"}` when the device token lacks the required scope.
