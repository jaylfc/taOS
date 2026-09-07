### Fixed: Add agent self-service routes to _AGENT_TOKEN_PATHS

Added `/api/agents/me/models` and `/api/agents/me/model` to `_AGENT_TOKEN_PATHS` in
`tinyagentos/auth_middleware.py` so that agent LiteLLM keys (Bearer tokens) are accepted
for agent self-service routes. Previously, AuthMiddleware rejected these requests with 401
before the handler could authenticate the agent key.