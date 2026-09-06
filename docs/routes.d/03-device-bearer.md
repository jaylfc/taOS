# Device bearer self-service (second, narrower passthrough)

<!-- Beyond the EXEMPT_PATHS entry for GET /api/share/destinations, a paired device may call a small fixed set of routes with its scoped bearer -->

## Properties

- Device prefix matching: only tokens carrying `taosdev_` match; previously any bearer matched, shadowing valid sessions (401 for a logged-in user's unrelated `Authorization` header)
- Allowlist is method-and-path anchored: `GET /api/devices`, `DELETE /api/devices/{id}`, `POST /api/decisions` are deliberately NOT on it (session-only)
- Device identity always comes from the verified bearer, never the path or body; a device is never admin

## Auth model

- Caller sends `Authorization: Bearer <scoped_token>` (issued at `POST /api/devices/register`); browser sessions and agent JWTs are not accepted
- The path is in `EXEMPT_PATHS` (`tinyagentos/auth_middleware.py`): middleware passes `user_id=None`, `current_user_or_device` resolves the device
- CSRF: registered on the router (`dependencies=_csrf`) so future unsafe-method routes inherit the double-submit check; GET is exempt as safe

## Coverage

- `agent_chat` destinations resolve via the agent registry (exact canonical_id, then a slug lookup bounded to the `-YYYYMMDD-HHMMSS` tail); an agent with no registry row resolves nothing and its DM is omitted

## Response shape

```json
{"destinations": [
  {"kind": "library", "id": "library", "label": "Library"},
  {"kind": "project_files", "id": "<project-slug>", "label": "<project name>"},
  {"kind": "agent_chat", "id": "<agent-slug>", "label": "<display name>"}
]}
```
