# Getting Started — an agent's first taOS request

This guide takes an agent from "I have a token" to "I successfully called the
API and got a documented response."

## 1. Get your token

Per-agent API tokens are issued when an agent is deployed and injected as the
`TAOS_TOKEN` environment variable inside the agent's container. If your agent
is running with this env var set, you're ready.

To issue a token explicitly (e.g. for an external agent):

```bash
curl -X POST http://localhost:6969/api/agents/<your-agent-name>/token/issue
```

Response (the plaintext token is returned **once** — store it):

```json
{"token": "taos_agent_xxx…", "issued_at": "2026-05-12T13:00:00Z"}
```

A subsequent `GET /api/agents/<your-agent-name>` will only return
`has_token: true` plus the `issued_at` timestamp; the plaintext is not
recoverable.

## 2. Make your first call

List the agents your user can see:

```bash
curl -H "Authorization: Bearer $TAOS_TOKEN" http://localhost:6969/api/agents
```

Response (the agent dict shape evolves; the fields below are the stable core):

```json
[
  {
    "name": "code-review-agent",
    "host": "192.0.2.10",
    "qmd_index": "code-review",
    "color": "#7f5af0",
    "display_name": "Code Review Agent"
  }
]
```

The same call via `taosctl` (lands in Pass 1 Task 24):

```bash
taosctl agents list
```

## 3. Discover the surface

`GET /api` returns the discovery index — top-level route prefixes with a
human title and a `doc_url` pointing into this directory:

```bash
curl http://localhost:6969/api
```

```json
{
  "routes": [
    {"prefix": "/api/agents", "title": "Agents",
     "doc_url": "/docs/agents/recipes/managing-agents.md"},
    {"prefix": "/api/ui/notify", "title": "UI: notify the user",
     "doc_url": "/docs/agents/recipes/notifying-the-user.md"}
  ]
}
```

The full per-endpoint contract lives at `/openapi.json`. Combine the two:
`/api` tells you what surfaces exist; `/openapi.json` tells you what each
endpoint expects and returns.

## 4. When something errors

Every error response in Pass 1 scope follows the same shape:

```json
{
  "error": "scope_denied",
  "detail": "Token scope does not cover 'agents.create'.",
  "fix": "Reissue the token with a wider scope (e.g. ['*'] for full access) via POST /api/agents/{name}/token/issue, or have the operator widen the agent's permissions.",
  "doc_url": "/docs/agents/concepts/permissions"
}
```

The `fix` is the concrete next step. The `doc_url` is where to learn more.
Always read them — errors are designed to be self-healing.

Common slugs you'll see:

- `agent_not_found` (404) — the name in the URL doesn't match a configured
  agent.
- `scope_denied` (403) — your token's scope doesn't cover the action.
- `invalid_token` (401) — the bearer is unknown or has been revoked.
- `validation_error` (422) — the request body failed Pydantic validation.
  See `/openapi.json` for the schema.

## Next steps

- **Recipes:** [`recipes/managing-agents.md`](recipes/managing-agents.md),
  [`recipes/notifying-the-user.md`](recipes/notifying-the-user.md).
- **Permissions:** [`concepts/permissions.md`](concepts/permissions.md).
