# Cluster node revoke, block, unblock and fleet mutations (admin-only)

<!-- Route module `tinyagentos/routes/cluster.py`. Admin only; no registry scope reaches these -->

## API endpoints

### POST /api/cluster/workers/{name}/revoke

- Kills the node's HMAC signing key; register and heartbeat are rejected until it re-pairs (announce/confirm/claim) for a fresh key
- Answers `{"revoked": true, "changed": <bool>}`

### POST /api/cluster/workers/{name}/block

- Revokes the key AND refuses re-pairing until an admin unblocks (acts at the pairing gate, not the auth gate)

### POST /api/cluster/workers/{name}/unblock

- Clears the blocked flag only; the old signing key stays dead, so the node still has to re-pair

### Other fleet mutations (same admin gate)

`DELETE /api/cluster/workers/{name}`, `POST .../{name}/deploy`, `POST .../{name}/remote`, `POST /api/cluster/move`, `/route`, `/promote-archived`: `403 {"detail": "forbidden"}` unless admin session or host local token. Worker-facing paths (heartbeat, pairing, leases, capabilities) keep their HMAC / possession gates.

## Common behaviour

- `404` when the node is absent from the PAIRING store; `503` when the pairing store is unavailable
- Revoke and block mark the in-memory worker **offline immediately** so the scheduler stops routing to it
- Blocked devices keep consuming a per-user slot (`list_for_user` returns `revoked=0 OR blocked=1`) until unblocked
