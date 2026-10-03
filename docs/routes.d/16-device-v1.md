# Device API v1

Single source of truth for the firmware repo. All routes under `/api/devices`
are bearer-authenticated (not session-authenticated).

## Pairing

### POST /api/devices/pair-requests

**Scope:** public (no session/device bearer). Opaque `pair_request_id` is capability for polling/Decision.

**Length caps:** `platform` required (ios/android/watchos/wearos/embedded/linux/windows/macos; else 400); `display_name` optional max 120; `push_token` optional max 4096.

**Request:** `{"platform": "ios", "display_name": "My Phone", "push_token": "apns-token"}`

**Response 200:** `{"pair_request_id": "uuid", "verify_code": "123456", "server_cert_fingerprint": "ab:cd:ef:..."}`

`server_cert_fingerprint` = SHA-256 colon-hex of controller TLS cert (DER). Cross-check only: device MUST display/pin fingerprint from own handshake, abort with `pair_fingerprint_mismatch` if differs.

**Errors:** `400` invalid platform/too large; `409` no admin; `429` too many pending.

### GET /api/devices/pair-requests/{pair_request_id}

**Scope:** public (capability = path param).

**Pending:** `{"id": "uuid", "status": "pending", "platform": "ios", "display_name": "My Phone", "push_token": "apns-token", "verify_code": null, "device": null, "scoped_token": null, "created_at": "...", "expires_at": "..."}`

`verify_code` only on POST creation; never on poll/Decision. Decision includes `server_cert_fingerprint` in metadata.

**Accepted:** `{"status": "accepted", "device": {"device_id": "uuid", "platform": "ios", "display_name": "My Phone", "user_id": "admin-uuid", "registered_at": 1727640000}, "scoped_token": "taosdev_...", "created_at": "...", "expires_at": "..."}`

`scoped_token` one-time: only on first accepted poll.

**Denied/expired:** `{"status": "denied", "device": null, "scoped_token": null}`

**Error:** `404` unknown `pair_request_id`.

## TLS listener

Embedded (`platform: embedded`) MUST use TLS on **6974** (`TAOS_DEVICE_TLS_PORT`). Plain HTTP 6969 refuses embedded bearers: `{"error": "device_tls_required"}`.

Phones/watches/legacy stay on plain HTTP 6969.

Controller generates self-signed cert on first boot, persists to data dir (`device_tls.crt/key`, 0600). Fingerprint survives restarts; no auto-rotation in v1; re-pair to change pin.

### Device pinning rule

1. Device opens TLS to `:<TAOS_DEVICE_TLS_PORT>`, computes SHA-256 fingerprint of server cert.
2. Displays fingerprint for manual verification.
3. Includes POST response `server_cert_fingerprint` in same display.
4. If differ, device MUST abort locally with `pair_fingerprint_mismatch` (device-side; server never returns this).

`server_cert_fingerprint` in POST is server-side cross-check only; authoritative value is device's handshake computation.

## Errors

| HTTP | Detail | Meaning |
|---|---|---|
| 400 | `invalid_platform` | Unknown `platform`. |
| 400 | `pair_request_too_large` | Body exceeds cap. |
| 403 | `device_tls_required` | Embedded bearer on plain HTTP. |
| 404 | `pair_request_not_found` | Unknown `pair_request_id`. |
| 409 | `no_admin_for_pairing` | No admin user. |
| 409 | `pair_request_expired` | Approval after TTL. |
| 409 | `pair_request_already_accepted` | Duplicate approve. |
| 429 | `pair_request_pending_cap_exceeded` | Too many open. |