# Recipe: Notifying the user

**Goal:** send a notification from the calling agent to the user it acts for.

**Prerequisites:** token with `ui.notify` scope (the default `["*"]` covers
it). The endpoint is agent-only — session-cookie callers get 401
`auth_required`.

---

## Send a notification

**API:**

```bash
curl -X POST -H "Authorization: Bearer $TAOS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Build complete",
    "body": "PR #449 merged.",
    "priority": "normal"
  }' \
  http://localhost:6969/api/ui/notify
```

**CLI:**

```bash
taosctl ui notify --title "Build complete" --body "PR #449 merged."
```

**Expected response:**

```json
{"delivered": true, "notification_id": "ntf_aB3-xY7q"}
```

The `notification_id` is a client-side tracking token — useful for log
correlation. It does NOT correspond to a stable DB row identifier in Pass 1.

**Verifying:** the notification appears in the desktop notification panel.
To verify programmatically:

```bash
curl -H "Authorization: Bearer $TAOS_TOKEN" \
  "http://localhost:6969/api/notifications?limit=10"
```

Look for an entry where `source` starts with `agent:` followed by your agent
name (e.g. `source: "agent:code-review-agent"`). The `title` and `message`
fields hold the values you sent.

---

## Optional fields

- **`priority`** (string, default `"normal"`): one of `low`, `normal`,
  `high`. Stored in the notification's `level` field so the desktop panel
  can render high-priority notifications more aggressively.
- **`app_origin`** (string, optional): attribution shown to the user.
  Defaults to the agent's name. Always prefixed with `agent:` in the
  stored `source` field for audit consistency.

> **Pass 2 (not yet in the request schema):** `action_url` (deep link
> the user lands on when they tap the notification). It lands alongside
> the multi-user NotificationStore migration so it has a column to be
> stored in — accepting it now would silently discard the caller's intent.

---

## Common errors

- **`invalid_priority` (400):** `priority` was not in `{low, normal, high}`.
  Omit the field to default to `"normal"`.
- **`scope_denied` (403):** token scope doesn't cover `ui.notify`. Reissue
  with `["*"]` or include `"ui.notify"` in the scope list.
- **`auth_required` (401):** no bearer token (e.g. only a session cookie).
  Issue an agent bearer with
  `POST /api/agents/{name}/token/issue` and resend.
- **`validation_error` (422):** missing `title` or `body`, or one was the
  wrong type. Check `/openapi.json#components/schemas/UiNotifyRequest`.

---

## Scoping note

ui.notify is the first agent-to-UI primitive. In Pass 1 it writes to the
existing single-user notification store with `source = "agent:<name>"` so
the panel can attribute the notification correctly. Per-user routing —
"only u1 sees this notification, not u2" — lands when the NotificationStore
gains multi-user columns in Pass 2. The agent-facing contract here is
forward-compatible: the same request body works against the multi-user
store, the `source` semantics just become stricter.

## See also

- [`../concepts/permissions.md`](../concepts/permissions.md) for the scope
  model.
- [`managing-agents.md`](managing-agents.md) for the agent-token lifecycle.
