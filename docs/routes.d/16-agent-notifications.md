# Agent notifications (`notifications_write` grant)

`POST /api/notifications` body: `title` (max 120), `message` (max 1000), `level` (`info`/`warning`), `source` (ignored), `data.project_id` (optional).

- Admits exactly `POST /api/notifications` for agent registry bearers. `GET`/mark-read session-only.
- Verifies JWT + grant + project binding via `check_agent_scope_for_project`.
- Global grant → instance admin(s). Per-project grant → project owner only.
- `source` from grant. Store row: `agent:<canonical_id>`. `data.from_agent` = canonical_id.
- `info`/`warning` only. `error` → `400`.
- Caps: `title` ≤ 120, `message` ≤ 1000, `data` ≤ 4 KB. Violations → `422`.
- Rate limit: 10 posts/10min/canonical_id. Exceed → `429 {"error": "rate_limited", "retry_after": N}`.
- Delivery via `store.add`: SSE and web-push fire.
- Human path: `_require_admin` gate, any level, source from body.