# Cluster node revoke, block, unblock and fleet mutations (admin-only)

<!-- Route module `tinyagentos/routes/cluster.py`. Admin only; no registry scope reaches these -->

## API endpoints

### POST /api/cluster/workers/{name}/revoke

- Kills HMAC signing key; register/heartbeat rejected until re-pair (announce/confirm/claim) for fresh key
- Answers `{"revoked": true, "changed": <bool>}`

### POST /api/cluster/workers/{name}/block

- Revokes key AND refuses re-pair until admin unblocks (pairing gate, not auth gate)

### POST /api/cluster/workers/{name}/unblock

- Clears blocked flag only; old key stays dead, node must re-pair

### Other fleet mutations (same admin gate)

`DELETE /api/cluster/workers/{name}`, `POST .../{name}/deploy`, `POST .../{name}/remote`, `POST /api/cluster/move`, `/route`, `/promote-archived`: `403 {"detail": "forbidden"}` unless admin session or host local token. Worker-facing (heartbeat, pairing, leases, capabilities) keep HMAC/possession gates.

## Common behaviour

- `404` node absent from PAIRING store; `503` pairing store unavailable
- Revoke/block mark in-memory worker **offline immediately** so scheduler stops routing
- Blocked devices consume per-user slot (`list_for_user` → `revoked=0 OR blocked=1`) until unblocked