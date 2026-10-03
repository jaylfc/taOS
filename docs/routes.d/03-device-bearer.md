# Device bearer self-service (second, narrower passthrough)

<!-- Beyond the EXEMPT_PATHS entry for GET /api/share/destinations, a paired device may call a small fixed set of routes with its scoped bearer -->

## Properties

- Device prefix matching: only `taosdev_` tokens match; previously any bearer matched, shadowing valid sessions
- Allowlist is method+path anchored: `GET /api/devices`, `DELETE /api/devices/{id}`, `POST /api/decisions` NOT on it (session-only)
- Device identity from verified bearer only, never path/body; device never admin

## Auth model

- Caller sends `Authorization: Bearer <scoped_token>` (issued at `POST /api/devices/register`); browser sessions/agent JWTs rejected
- Path in `EXEMPT_PATHS` (`auth_middleware.py`): middleware passes `user_id=None`, `current_user_or_device` resolves device
- CSRF on router (`dependencies=_csrf`) so future unsafe routes inherit double-submit; GET exempt as safe

## Coverage

- `agent_chat` destinations resolve via agent registry (exact canonical_id, then slug lookup bounded to `-YYYYMMDD-HHMMSS` tail); no registry row = nothing resolved, DM omitted

## Response shape

`{"destinations": [{"kind": "library", "id": "library", "label": "Library"}, {"kind": "project_files", "id": "<project-slug>", "label": "<project name>"}, {"kind": "agent_chat", "id": "<agent-slug>", "label": "<display name>"}]}`