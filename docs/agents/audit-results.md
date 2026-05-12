# AgentsApp Endpoint Audit — Pass 1 Results

Per-endpoint pass/fail against the agent-friendliness checklist
(see `docs/superpowers/specs/2026-05-12-agent-friendliness-audit-design.md` §"REST API audit checklist").

**Status as of 2026-05-12:** _in progress_

| Endpoint | URL shape | HTTP verb | OpenAPI complete | Errors actionable | Bearer auth | Idempotent | List shape | Flat response | Notes |
|---|---|---|---|---|---|---|---|---|---|
| GET /api/agents | ✅ | ✅ | ✅ | ✅ | _pending_ | N/A | _pending_ | _pending_ | List endpoint — verify pagination cursor |
| POST /api/agents | _pending_ | _pending_ | ✅ | ✅ | _pending_ | _pending_ | N/A | _pending_ | Create endpoint — add Idempotency-Key support |
| GET /api/agents/{name} | _pending_ | _pending_ | ✅ | ✅ | _pending_ | N/A | N/A | _pending_ | Surfaces has_token (Task 6 ✅) |
| PUT /api/agents/{name} | _pending_ | _pending_ | ✅ | ✅ | _pending_ | N/A | N/A | _pending_ | |
| DELETE /api/agents/{name} | _pending_ | _pending_ | ✅ | ✅ | _pending_ | N/A | N/A | _pending_ | Cascades token revoke (Task 7 ✅) |
| POST /api/agents/{name}/start | _pending_ | _pending_ | ✅ | ✅ | _pending_ | _pending_ | N/A | _pending_ | |
| POST /api/agents/{name}/stop | _pending_ | _pending_ | ✅ | ✅ | _pending_ | _pending_ | N/A | _pending_ | |
| POST /api/agents/{name}/pause | _pending_ | _pending_ | ✅ | ✅ | _pending_ | _pending_ | N/A | _pending_ | |
| POST /api/agents/{name}/restart | _pending_ | _pending_ | ✅ | ✅ | _pending_ | _pending_ | N/A | _pending_ | |
| GET /api/agents/{name}/logs | _pending_ | _pending_ | ✅ | ✅ | _pending_ | N/A | _pending_ | _pending_ | |
| PUT /api/agents/{name}/permissions | _pending_ | _pending_ | ✅ | ✅ | _pending_ | N/A | N/A | _pending_ | Add scope field (Task 14) |
| POST /api/agents/deploy | _pending_ | _pending_ | ✅ | ✅ | _pending_ | _pending_ | N/A | _pending_ | Add Idempotency-Key support (Task 12) |
| POST /api/agents/{name}/token/issue | ✅ | ✅ | ✅ | ✅ | _pending_ | N/A | N/A | ✅ | Returns plaintext once (Task 6 ✅) |
| DELETE /api/agents/{name}/token | ✅ | ✅ | ✅ | ✅ | _pending_ | N/A | N/A | ✅ | Cascade on agent delete (Task 7 ✅) |
| GET /api | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | N/A | N/A | _pending_ | Discovery index — new endpoint (Task 13) |
| POST /api/ui/notify | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | _pending_ | N/A | _pending_ | First agent-to-UI primitive (Task 15) |

## Remediations

Tasks 10–15 of the implementation plan address each `_pending_` cell. After
each remediation lands, replace `_pending_` with ✅ (or ❌ N/A with reason).

## Deferred to a later pass

- `response_model=` for success responses on agent CRUD endpoints — agent
  dicts are still loose-typed in Pass 1; locking the schema would either
  regress existing fields or land an empty/almost-empty pydantic model.
