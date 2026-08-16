# Device bearer self-service (second, narrower passthrough)

<!-- Beyond the EXEMPT_PATHS entry for GET /api/share/destinations, a paired device may call a small fixed set of routes with its scoped bearer -->

## Properties that hold this together

### Device prefix matching

- The passthrough matches only tokens carrying the device prefix (`taosdev_`)
- Matching any bearer previously shadowed valid sessions: a logged-in user who happened to send an unrelated `Authorization` header got 401 on every one of these routes

### Allowlist is method-and-path anchored

- `GET /api/devices`, `DELETE /api/devices/{id}`, `POST /api/decisions` are deliberately NOT on it and stay session-only

### Device identity

- Always comes from the verified bearer, never from the path or body
- A device is never admin

## Auth model

- Caller sends `Authorization: Bearer <scoped_token>` (issued at `POST /api/devices/register`)
- Browser sessions and agent JWTs are not accepted
- The path is listed in `EXEMPT_PATHS` in `tinyagentos/auth_middleware.py` so the session cookie gate does not apply
- The middleware simply lets the request through with `user_id=None` so the route's own `current_user_or_device` dependency resolves the device

### CSRF

- Registered on the router (`dependencies=_csrf`) so future unsafe-method routes inherit the double-submit check
- The GET is exempt because safe methods always are

## Coverage

- `agent_chat` destinations resolve through the agent registry (exact canonical_id, then a slug lookup bounded to the canonical `-YYYYMMDD-HHMMSS` tail)
- Only registry-backed agents appear; a plain deployed agent with no registry row resolves nothing and its DM is omitted

## Response shape

```json
{
  "destinations": [
    {"kind": "library", "id": "library", "label": "Library"},
    {"kind": "project_files", "id": "<project-slug>", "label": "<project name>"},
    {"kind": "agent_chat", "id": "<agent-slug>", "label": "<display name>"}
  ]
}
```