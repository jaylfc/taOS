# Cluster node revoke, block and unblock (admin-only)

<!-- Route module `tinyagentos/routes/cluster.py`. Admin session only (`_require_admin`); no registry scope reaches these -->

## API endpoints

### POST /api/cluster/workers/{name}/revoke

- Kills the node's HMAC signing key
- Subsequent register and heartbeat requests are rejected
- The node may re-pair through the normal announce/confirm/claim flow to obtain a fresh key
- Answers `{"revoked": true, "changed": <bool>}`

### POST /api/cluster/workers/{name}/block

- Revokes the key AND refuses re-pairing until an admin unblocks
- The distinction from revoke: acts at the pairing gate, not merely at the auth gate
- So it cannot come back on its own

### POST /api/cluster/workers/{name}/unblock

- Clears the blocked flag only
- The old signing key stays dead, so the node still has to re-pair for a fresh one
- Unblock is permission to return, not restoration of access

## Common behaviour

- `404` when the node is absent from the PAIRING store (was never paired)
- `503` when the pairing store is unavailable, kept distinct from `404`
- Revoke and block mark the in-memory worker **offline immediately** so the scheduler stops routing tasks to it
- Blocked devices keep consuming a per-user slot: `list_for_user` returns rows where `revoked=0 OR blocked=1`, so a blocked device counts against `_MAX_DEVICES_PER_USER` until it is unblocked