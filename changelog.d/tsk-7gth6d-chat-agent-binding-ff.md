### Fixed

- Bound agent reactions now always forward `reactor_type=agent` to semantic triggers, preventing an agent from impersonating a user when reacting.
- Bound agent typing is recorded with `kind=agent` instead of `human` so the registry and broadcasts correctly distinguish agents from users.
- `POST /api/chat/messages/{id}/delta` and `POST /api/chat/messages/{id}/state` now return 403 when the target message does not exist or is not authored by the bound agent, closing a fall-through gap.