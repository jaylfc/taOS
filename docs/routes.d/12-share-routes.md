# User resource sharing (share routes)

<!-- Users can share resources with each other through the `/api/shares` endpoints in `tinyagentos/routes/user_shares.py`. The consent loop mirrors the external-agent consent pattern -->

## API endpoints

### POST /api/shares

- Body: `{resource_type, resource_id, to_username, permission}`
- Shares a resource with another user by username (resolved via AuthManager); self-share is `400`
- Duplicate shares (same owner, resource, target, permission) are idempotent

### GET /api/shares?direction=out|in

- `out` (default): shares the user owns; `in`: shares where the user is the target

### POST /api/shares/{id}/accept

- Accept a pending share (target user only); afterwards `user_can_access()` returns True for that resource

### POST /api/shares/{id}/deny

- Deny a pending share (target user only); the row is kept with `status=denied` for audit

### DELETE /api/shares/{id}

- Revoke a share; owner or admin only (`require_owner_or_admin` against the share's `owner_user_id`)