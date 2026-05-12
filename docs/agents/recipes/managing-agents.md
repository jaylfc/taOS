# Recipe: Managing Agents

Task-oriented walkthrough of the AgentsApp surface. Every section follows
the same shape: Goal, Prerequisites, Steps (API + CLI side-by-side), Expected
response, Verifying, Common errors. Fenced `bash` blocks here are executed
by the conformance test suite — if the surface changes, change the recipe
and the tests stay green.

The `taosctl` CLI commands referenced below land in Pass 1 Task 24; the API
calls work today.

---

## List agents

**Goal:** see which agents your user can access.

**Prerequisites:** token with `agents.list` scope (the default `["*"]` covers
it).

**API (bash):**

```bash-skip
curl -H "Authorization: Bearer $TAOS_TOKEN" http://localhost:6969/api/agents
```

**API (HTTP):**

```http
GET /api/agents HTTP/1.1
Authorization: Bearer $TAOS_TOKEN
```

**CLI:**

```bash-skip
taosctl agents list
```

**Expected response:**

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

**Verifying:** every entry has at least `name`, `host`, and `qmd_index`. Live
container state is queried separately via `GET /api/agents/containers`.

**Common errors:**

- `scope_denied` (403): token doesn't include `agents.list`. Reissue with
  wider scope.

---

## Get one agent

**Goal:** fetch the full record for one agent, including `has_token` and
`issued_at`.

**Prerequisites:** token with `agents.read` scope.

**API (bash):**

```bash-skip
curl -H "Authorization: Bearer $TAOS_TOKEN" \
  http://localhost:6969/api/agents/test-agent
```

**API (HTTP):**

```http
GET /api/agents/test-agent HTTP/1.1
Authorization: Bearer $TAOS_TOKEN
```

**CLI:**

```bash-skip
taosctl agents get <agent-name>
```

**Expected response:**

```json
{
  "name": "code-review-agent",
  "host": "192.0.2.10",
  "qmd_index": "code-review",
  "color": "#7f5af0",
  "has_token": true,
  "issued_at": "2026-05-12T13:00:00Z",
  "last_used_at": "2026-05-12T13:05:42Z"
}
```

`has_token` is `false` for agents that have never been issued one. The
plaintext token is never returned here — only `POST /token/issue` returns
it, and only once.

**Common errors:**

- `agent_not_found` (404): the name in the URL doesn't match a configured
  agent. List agents to see valid names.

---

## Create an agent

**Goal:** register a new agent config row.

**Prerequisites:** token with `agents.create` scope. Optional but
recommended: send an `Idempotency-Key` header to make retries safe.

**API (bash):**

```bash-skip
curl -X POST -H "Authorization: Bearer $TAOS_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: create-$(uuidgen)" \
  -d '{
    "name": "my-agent",
    "host": "192.0.2.11",
    "qmd_index": "my-agent",
    "color": "#7f5af0"
  }' \
  http://localhost:6969/api/agents
```

**API (HTTP):**

```http
POST /api/agents HTTP/1.1
Authorization: Bearer $TAOS_TOKEN
Content-Type: application/json
Idempotency-Key: recipe-conf-create-2

{"name": "recipe-conf-create-2", "host": "192.0.2.11", "qmd_index": "recipe-test"}
```

**CLI:**

```bash-skip
taosctl agents create --name my-agent --host 192.0.2.11 --qmd-index my-agent
```

**Expected response:**

```json
{"status": "created", "name": "my-agent", "display_name": "my-agent"}
```

**Verifying:** call `GET /api/agents/my-agent` and confirm the entry exists.
Then issue a token with `POST /api/agents/my-agent/token/issue`.

**Note on duplicate names:** if `my-agent` already exists, the slug is
auto-suffixed (`my-agent-2`, `my-agent-3`, …). To get the same response on
a retry, send the same `Idempotency-Key`.

**Common errors:**

- `invalid_agent_name` (400): the name failed validation (empty, illegal
  characters). Pick a different name.
- `validation_error` (422): required field missing. Check the body against
  `/openapi.json#components/schemas/AgentCreate`.

---

## Issue a token

**Goal:** issue an API token for an agent. Revokes any prior active token.

**Prerequisites:** token with `agents.token.issue` scope. (The
operator-issued bearer for token management is different from the
agent-owned bearer that the new token represents.)

**API (bash):**

```bash-skip
curl -X POST -H "Authorization: Bearer $TAOS_TOKEN" \
  http://localhost:6969/api/agents/test-agent/token/issue
```

**API (HTTP):**

```http
POST /api/agents/test-agent/token/issue HTTP/1.1
Authorization: Bearer $TAOS_TOKEN
```

**CLI:**

```bash-skip
taosctl agents token-issue <agent-name>
```

**Expected response (the plaintext is returned ONCE — store it now):**

```json
{"token": "taos_agent_xxx…", "issued_at": "2026-05-12T13:00:00Z"}
```

**Verifying:** call any authenticated endpoint with `Authorization: Bearer
taos_agent_xxx…` and confirm it succeeds. A subsequent
`GET /api/agents/<agent-name>` shows `has_token: true` but never the
plaintext.

**Common errors:**

- `agent_not_found` (404): no agent with that name.
- `scope_denied` (403): missing `agents.token.issue` scope.

---

## Revoke a token

**Goal:** invalidate an agent's active token immediately.

**Prerequisites:** token with `agents.token.revoke` scope.

**API (bash):**

```bash-skip
curl -X DELETE -H "Authorization: Bearer $TAOS_TOKEN" \
  http://localhost:6969/api/agents/test-agent/token
```

**API (HTTP):**

```http
DELETE /api/agents/test-agent/token HTTP/1.1
Authorization: Bearer $TAOS_TOKEN
```

**CLI:**

```bash-skip
taosctl agents token-revoke <agent-name>
```

**Expected response:** 204 No Content. The previous plaintext returns 401
`invalid_token` on every subsequent call.

**Verifying:** call any authenticated endpoint with the old bearer; confirm
401. Then issue a fresh token if access should be restored.

---

## Start / stop / pause / restart

<!-- bash-skip only: these endpoints drive the LXC container lifecycle and
     require incus on the host — not testable via the ASGI test transport. -->

**Goal:** drive the container lifecycle.

**Prerequisites:** token with `agents.lifecycle` scope.

**API:**

```bash-skip
curl -X POST -H "Authorization: Bearer $TAOS_TOKEN" \
  http://localhost:6969/api/agents/<agent-name>/start
```

Substitute `start` with `stop`, `pause`, or `restart` as needed. Bulk
operations: `POST /api/agents/bulk/{start|stop|restart}` apply to every
configured agent.

**CLI:**

```bash-skip
taosctl agents start <agent-name>
taosctl agents stop <agent-name>
taosctl agents pause <agent-name>
taosctl agents restart <agent-name>
```

**Verifying:** poll `GET /api/agents/containers` and check the `status` field
for the matching container name (`taos-agent-<slug>`).

**Common errors:**

- `agent_not_found` (404): wrong name.
- `scope_denied` (403): missing `agents.lifecycle`.

---

## Read recent logs

<!-- bash-skip only: logs endpoint proxies to the LXC container runtime —
     not testable via the ASGI test transport without a live container. -->

**Goal:** fetch the most recent log lines from an agent's container.

**Prerequisites:** token with `agents.logs` scope.

**API:**

```bash-skip
curl -H "Authorization: Bearer $TAOS_TOKEN" \
  "http://localhost:6969/api/agents/<agent-name>/logs?lines=100"
```

**CLI:**

```bash-skip
taosctl agents logs <agent-name> --lines 100
```

**Expected response:** a JSON object with the latest log lines (shape
documented in `/openapi.json`).

---

## Update agent permissions

**Goal:** change non-secret agent metadata (host, qmd_index, color, emoji,
`can_read_user_memory`).

**Prerequisites:** token with `agents.update` scope.

**API (bash):**

```bash-skip
curl -X PUT -H "Authorization: Bearer $TAOS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"color": "#ff5a36"}' \
  http://localhost:6969/api/agents/test-agent
```

**API (HTTP):**

```http
PUT /api/agents/test-agent HTTP/1.1
Authorization: Bearer $TAOS_TOKEN
Content-Type: application/json

{"color": "#ff5a36"}
```

**CLI:**

```bash-skip
taosctl agents update <agent-name> --color "#ff5a36"
```

**Verifying:** `GET /api/agents/<agent-name>` reflects the change.

---

## Delete (archive) an agent

**Goal:** remove an agent from the active list. The default flow archives
(snapshot + tombstone) rather than hard-deleting, so a future
`POST /api/agents/restore/<archive-id>` can bring it back.

**Prerequisites:** token with `agents.delete` scope.

**API (bash):**

```bash-skip
curl -X DELETE -H "Authorization: Bearer $TAOS_TOKEN" \
  http://localhost:6969/api/agents/recipe-conf-delete-target
```

**API (HTTP):** create the target agent first, then delete it.

```http
POST /api/agents HTTP/1.1
Authorization: Bearer $TAOS_TOKEN
Content-Type: application/json
Idempotency-Key: recipe-conf-delete-pre-8

{"name": "recipe-conf-delete-target", "host": "192.0.2.250", "qmd_index": "recipe-test"}
```

```http
DELETE /api/agents/recipe-conf-delete-target HTTP/1.1
Authorization: Bearer $TAOS_TOKEN
```

**CLI:**

```bash-skip
taosctl agents delete <agent-name>
```

**Expected response:** an archive-result dict with the archive id and
timestamp. If the container had no snapshot-worthy state (orphan config
row), the agent is hard-deleted instead and the response indicates that.

**Side effect:** the agent's API token is revoked automatically as part of
the delete (cascade). The previous bearer stops authenticating immediately.

**Common errors:**

- `agent_not_found` (404): no agent with that name.
- `archive_failed` (5xx): snapshot or container stop step failed. The agent
  is left in the live list so you can fix container state and retry.
