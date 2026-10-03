# Device API v1

Single source of truth for the firmware repo. All routes under `/api/devices`
are bearer-authenticated (not session-authenticated).

## Pairing

### POST /api/devices/pair-requests

**Scope:** public (no session or device bearer required). The opaque
`pair_request_id` is the capability; polling and the Decision use it.

**Length caps / validation:**

- `platform`: required, one of `ios`, `android`, `watchos`, `wearos`,
  `embedded`, `linux`, `windows`, `macos`. Unknown returns 400.
- `display_name`: optional, max 120 chars.
- `push_token`: optional, max 4096 chars.

**Request:**

```json
{
  "platform": "ios",
  "display_name": "My Phone",
  "push_token": "apns-token"
}
```

**Response 200:**

```json
{
  "pair_request_id": "uuid",
  "verify_code": "123456",
  "server_cert_fingerprint": "ab:cd:ef:..."
}
```

`server_cert_fingerprint` is the SHA-256 colon-hex fingerprint of the
controller's own TLS certificate (computed over the DER encoding). It is
returned as a cross-check only: the device MUST display and pin the
fingerprint it computes from its own TLS handshake, and MUST abort with
`pair_fingerprint_mismatch` if the handshake value differs from this field.

**Error codes:**

- `400` -- invalid `platform` or request too large.
- `409` -- no admin user exists yet (instance not onboarded).
- `429` -- too many pending requests (`DEVICE_PAIR_REQUESTS_PENDING_CAP`).

### GET /api/devices/pair-requests/{pair_request_id}

**Scope:** public (capability is the path parameter).

**Response 200 (pending):**

```json
{
  "pair_request_id": "uuid",
  "status": "pending"
}
```

`verify_code` is present only on the creation response (POST); it is NEVER
surfaced on the poll (GET) or in the Decision. The Decision raised to the
admin includes `server_cert_fingerprint` in its metadata for reference.

**Response 200 (accepted):**

```json
{
  "pair_request_id": "uuid",
  "status": "accepted",
  "device": {
    "device_id": "uuid",
    "platform": "ios",
    "display_name": "My Phone",
    "user_id": "admin-uuid",
    "registered_at": 1727640000
  },
  "scoped_token": "taosdev_..."
}
```

`scoped_token` is one-time: it appears only on the first accepted poll.

**Response 200 (denied / expired):**

```json
{
  "pair_request_id": "uuid",
  "status": "denied"
}
```

An expired request returns the same shape with `"status": "expired"`.

**Error codes:**

- `404` -- unknown `pair_request_id`.

## TLS listener

Embedded-platform devices (platform `embedded`) MUST connect to the controller
over TLS on port **6974** (`TAOS_DEVICE_TLS_PORT`). Plain HTTP on port 6969
refuses embedded bearer tokens with:

```json
{"detail": {"error": "device_tls_required"}}
```

Phones, watches, and other legacy platforms continue to work over plain HTTP
on port 6969.

The controller generates a self-signed certificate on first boot and persists
it under the data directory (`device_tls.crt`, `device_tls.key`, mode 0600).
The fingerprint survives restarts. There is no automatic rotation in v1;
re-pairing is the only way the pin changes.

### Device pinning rule

1. The device opens a TLS connection to `:<TAOS_DEVICE_TLS_PORT>` and computes
   the SHA-256 fingerprint of the server certificate from the handshake.
2. The device displays the fingerprint to the user for manual verification.
3. The device includes the POST `/api/devices/pair-requests` response field
   `server_cert_fingerprint` in the same display.
4. If the two fingerprints differ, the device MUST abort pairing locally with
   error `pair_fingerprint_mismatch`. This is a device-side abort; the server
   never returns this error code.

The `server_cert_fingerprint` field in the POST response is a server-side
cross-check only; the authoritative value is always the one the device computes
from its own handshake.

## Errors

| HTTP status | Detail | Meaning |
|---|---|---|
| 400 | `platform must be one of [...]` | Unknown or missing `platform` value. |
| 400 | `push_token is not accepted for <platform> devices` | Push token supplied for a no-push platform. |
| 403 | `{"detail": {"error": "device_tls_required"}}` | Embedded bearer on plain HTTP. |
| 404 | `pair request not found` | Unknown `pair_request_id`. |
| 409 | `no admin exists to approve pairing requests` | No admin user exists yet (instance not onboarded). |
| 409 | `{"error": "already answered or not pending"}` | Duplicate or late approval on a pairing Decision. |
| 422 | FastAPI validation error | Body field exceeds `max_length` (e.g. `push_token` over 4096 chars). |
| 429 | `too many pending pair requests (...)` | `DEVICE_PAIR_REQUESTS_PENDING_CAP` reached. |

Clients must match on HTTP status, not on the detail text.
