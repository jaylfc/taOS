### Fixed

- Forged bearer tokens on deployed-agent runtime paths (e.g., `POST /api/trace`) now correctly return 401 instead of being accepted and writing trace rows under attacker-controlled agent names.
- Deployed agents presenting a registry JWT on desktop paths (`/api/desktop/command`, `/api/desktop/screenshot`, `/api/desktop/layout`, `/api/desktop/panel-shot`) no longer receive a `registry_jwt_deployed_agent` principal; the middleware rejects them at the auth layer.
- `GET /api/skill-exec/tools` again requires the `agent_name` query parameter (FastAPI 422 on omission) and returns 403 when a deployed agent's token claims a different `agent_name`.