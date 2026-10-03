# User resource sharing (share routes)

<!-- Users share resources via `/api/shares` in `tinyagentos/routes/user_shares.py`. Consent loop mirrors external-agent pattern -->

## API endpoints

### POST /api/shares

- Body: `{resource_type, resource_id, to_username, permission}`
- Shares resource with user by username (resolved via AuthManager); self-share `400`
- Duplicate shares (same owner, resource, target, permission) idempotent

### GET /api/shares?direction=out|in

- `out` (default): shares user owns; `in`: shares where user is target

### POST /api/shares/{id}/accept

- Accept pending share (target only); then `user_can_access()` returns True

### POST /api/shares/{id}/deny

- Deny pending share (target only); row kept with `status=denied` for audit

### DELETE /api/shares/{id}

- Revoke share; owner or admin only (`require_owner_or_admin` vs share's `owner_user_id`)