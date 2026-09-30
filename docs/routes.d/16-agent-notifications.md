# Agent notifications (`notifications_write` grant)

`POST /api/notifications` body: `title` (max 120 chars), `message` (max 1000 chars), `level` (`info`/`warning`), `source` (ignored on agent path), `data.project_id` (optional).

- Admits exactly `POST /api/notifications` for agent registry bearers. `GET` and mark-read stay session-only.
- Verifies JWT + grant + project binding via `check_agent_scope_for_project`.
- Global grant posts to instance admin(s). Per-project grant posts to that project's owner only.
- `source` from grant. Store row: `agent:<canonical_id>`. `data.from_agent` stamped to canonical_id.
- `info`/`warning` only. `error` returns 400.
- Caps: `title` <= 120, `message` <= 1000, `data` <= 4 KB. Violations return 422.
- Rate limit: 10 posts per 10 minutes per canonical_id. Exceeding returns 429 `{"error": "rate_limited", "retry_after": N}`.
- Delivery through `store.add`: SSE and web-push fire.
- Human path unchanged: `_require_admin` gate, any valid level, source from body.
