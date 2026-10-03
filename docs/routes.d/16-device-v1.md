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
  "id": "uuid",
  "status": "pending",
  "platform": "ios",
  "display_name": "My Phone",
  "push_token": "apns-token",
  "verify_code": null,
  "device": null,
  "scoped_token": null,
  "created_at": "2026-09-30T12:00:00Z",
  "expires_at": "2026-09-30T12:05:00Z"
}
```

`verify_code` is present only on the creation response (POST); it is NEVER
surfaced on the poll (GET) or in the Decision. The Decision raised to the
admin includes `server_cert_fingerprint` in its metadata for reference.

**Response 200 (accepted):**

```json
{
  "status": "accepted",
  "device": {
    "device_id": "uuid",
    "platform": "ios",
    "display_name": "My Phone",
    "user_id": "admin-uuid",
    "registered_at": 1727640000
  },
  "scoped_token": "taosdev_...",
  "created_at": "...",
  "expires_at": "..."
}
```

`scoped_token` is one-time: it appears only on the first accepted poll.

**Response 200 (denied / expired):**

```json
{
  "status": "denied",
  "device": null,
  "scoped_token": null
}
```

**Error codes:**

- `404` -- unknown `pair_request_id`.

## TLS listener

Embedded-platform devices (platform `embedded`) MUST connect to the controller
over TLS on port **6974** (`TAOS_DEVICE_TLS_PORT`). Plain HTTP on port 6969
refuses embedded bearer tokens with:

```json
{"error": "device_tls_required"}
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
| 400 | `invalid_platform` | Unknown `platform` value. |
| 400 | `pair_request_too_large` | Request body exceeds length cap. |
| 403 | `device_tls_required` | Embedded bearer on plain HTTP. |
| 404 | `pair_request_not_found` | Unknown `pair_request_id`. |
| 409 | `no_admin_for_pairing` | No admin user exists yet. |
| 409 | `pair_request_expired` | Approval arrived after TTL. |
| 409 | `pair_request_already_accepted` | Duplicate approve on an accepted request. |
| 429 | `pair_request_pending_cap_exceeded` | Too many open requests. |
