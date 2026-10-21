# Admin gates on global resources

<!-- `tinyagentos.auth_context.require_admin`: admin session or host local token, else `403` -->

Non-admin members get `403`; the host local token (`taosctl`, agents) passes.

| Router | Gated | Open / owner-scoped |
|---|---|---|
| secrets | list, get, add, update, delete, `categories` | `GET /api/secrets/agent/{agent}`: owner (registry `user_id`) or admin |
| system | `restart/prepare`, `ai-stack/restart`, non-loopback `prepare-shutdown` | loopback `prepare-shutdown`, `restart/status`, `hardware/refresh` |
| providers | create, patch, delete, `start`, `stop` | `GET /api/providers` (model pickers) with `api_key` stripped for non-admins |
| mcp | `start`/`stop`/`restart`, uninstall, `config` PUT, `env`, permission attach/detach, `/api/mcp/call`, marketplace install/uninstall/reload | list, logs, capabilities, permissions, `config` GET, marketplace browse/detail |
| agent-model-keys | `POST /api/agent-model-keys` mints only for agents the caller owns (admin: any) | |
