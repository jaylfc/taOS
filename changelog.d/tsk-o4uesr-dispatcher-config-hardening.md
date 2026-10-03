### Fixed

- Dispatcher config: `eligible_agents` must name an agent the target user owns and that is still active (422 otherwise), so another user's or a retired agent can no longer be stored as dispatchable.
- Dispatcher config: a signed-in browser carrying a stale or unrelated `Authorization: Bearer` header is served again instead of being refused, and the admin host local token works; `/api/dispatcher/config` is off the registry-JWT allowlist, so an agent token alone is a 401 rather than a 403.
- Dispatcher config PUT: unknown fields and mistyped values are rejected (422) instead of being silently ignored or coerced, and `boards` / `eligible_agents` are de-duplicated and capped at 200 ids.
- Dispatcher config: an admin targeting a user that does not exist gets a 404 instead of writing an orphan row, and a non-admin may name their own id in `?user_id=`.
- Dispatcher config: a config that has never been saved reports `updated_at: null` rather than a synthetic timestamp.