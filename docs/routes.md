<!-- GENERATED from docs/routes.d/ by scripts/build-routes-doc.py. Edit the source files, not this file. -->

# Project tasks (kanban board)

## Project tasks

Access the kanban board for a project. Granting `project_tasks` also makes the agent a project member.

### API endpoints

- `GET /api/projects/{pid}/tasks` — list tasks in a project
- `GET /api/projects/{pid}/tasks/ready` — list ready tasks
- `GET /api/projects/{pid}/tasks/{id}` — get a specific task
- `GET /api/projects/{pid}/tasks/{id}/comments` — list task comments
- `POST /api/projects/{pid}/tasks/{id}/claim` — claim a task (LEAD-only)
- `POST /api/projects/{pid}/tasks/{id}/release` — release a claimed task
- `POST /api/projects/{pid}/tasks/{id}/close` — close a task
- `POST /api/projects/{pid}/tasks/{id}/reopen` — reopen a closed task
- `GET /api/projects/tasks/{id}/context` — get task context

### Grant requirements

Granting `project_tasks` also makes the agent a project member.

### LEAD-only extensions

- `POST .../tasks/{id}/claimable` — add/remove the `claimable` label (LEAD-only)
- `POST .../tasks/{id}/unquarantine` — return a quarantined card to the open pool (LEAD-only)

---

# Agent API surface (scoped registry JWT)

## Scoped allowlist

Agents authenticate with their registry JWT (`Authorization: Bearer`) and reach exactly the routes their granted SCOPES allow, nothing else.

### project_tasks (the kanban board)

Granting `project_tasks` also makes the agent a project member.

### project_tasks_create

`POST /api/projects/{pid}/tasks` — author new cards. SEPARATE scope from `project_tasks`; off by default.

### project_tasks_update

`PATCH /api/projects/{pid}/tasks/{tid}` — whitelisted fields (title, body, labels, priority). own-or-lead cards only. SEPARATE from `project_tasks`; plain project_tasks token gets 403.

### canvas_read & canvas_write

Canvas routes require `canvas_read` or `canvas_write` scope. `GET .../canvas/elements`, `POST|PATCH|DELETE .../canvas/elements/{id}`.

### files_read & files_write

Files routes key on the project SLUG. `GET .../files/{path}`, `POST .../files/upload`, `DELETE .../files/{path}`.

### decisions_write

`POST /api/decisions` — raise a human-in-the-loop decision. `POST /api/decisions/{id}/answer/agent` — mirror an answer.

### a2a bus surface

`GET /api/a2a/bus/channels`, `GET /api/a2a/bus/messages`, `GET|POST /api/a2a/bus/stream`. a2a_receive token cannot post; a2a_send token is not thereby a reader.

### CONSENT KEY surface

`GET /v1/models` and `POST /v1/chat/completions` reachable without a session using a CONSENT KEY. No key, no resolution, OpenAI-shaped 401 otherwise. Only those two exact method+path pairs pass the middleware.

---

# Device bearer self-service (second, narrower passthrough)

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

---

# Project invite redeem route (link + PIN)

## Endpoints

### POST /api/projects/invites/redeem

Body: `{invite_id, pin, harness, label?}`

- Verifies the PIN (wrong PIN / expired / attempt-capped → 403; already redeemed / revoked → 409)
- Derives the agent handle `{project_slug}-{harness}[-{label}]`
- De-dupes it against active registry agents in the project
- Auto-approves through the shared `approve_request_record` helper (decided_by = the invite's creator) or leaves the request pending (manual mode)
- Returns a connection bundle plus `{request_id, agent_handle, poll_path}`
- `project_tasks` is force-included so a successful redeem always yields a project member

### GET /i/{invite_id}

Content-negotiated advert:

- `Accept: application/json` → gets the redeem contract (`{method, path, fields}`)
- Browser → gets a minimal HTML page
- No PIN check here; it only advertises the contract

## Connection bundle

- `controller.endpoints` — reachable addresses: non-loopback LAN IPv4s (priority ordered, operator override first) and the mesh (Tailscale) node IP when joined. No relay in Phase 1.
- `apis` — agent-JWT-reachable surface, scoped exactly to the granted scopes and mirroring the middleware canvas allowlist
- `delivery` — timed-check contract (`poll_path`, `stream_path`, `check_interval_secs` from the invite, `cursor: ts`, `filter: mentions+project`)
- `onboarding` + `guide_markdown` — personalized capability guide (repo link, agent manual links, scoped Projects/Canvas summary, the A2A authenticated-proxy contract, and explicit instructions)

See `docs/design/external-agent-project-invite.md` (issue #1780) for the full design; the bundle advertises canvas routes only when the corresponding scope was actually granted.

---

# OS change-event stream (`GET /api/os/events`, session-only)

## SSE stream characteristics

- `?kinds=a,b,c` — comma-separated allowlist of event kinds
- Omitted, empty, or naming no kind at all (`?kinds=`, `?kinds=%20`, `?kinds=,`) means every kind: the allowlist is derived first and an empty one means "no filter", because a truthy-but-blank parameter otherwise built a set that matched nothing and the stream delivered silence
- Filtering happens as events enter the per-connection buffer, not as they leave it, so an unrequested kind can never occupy a slot and evict something the subscriber did ask for
- At most 256 events are buffered per connection. Past that the OLDEST buffered event is dropped and the client is sent `{"kind": "events.lagged", "dropped": N}` — its cue to refetch rather than assume it saw everything
- A comment frame `:keepalive` is sent every 10 s so proxies do not close an idle stream
- Frames deliberately carry **no** SSE `id:` line. An `id:` is what makes a browser send `Last-Event-ID` on reconnect, and this endpoint ignores that header: resume is best-effort through the EventBus replay buffer (the last 32 events per channel, delivered on subscribe)
- The payload never crosses the wire: `id` is the event's trace id, so a subscriber learns that something changed and must refetch to learn what

## Desktop integration

- `desktop/src/hooks/use-os-events.ts`: `useOsEvents(kinds, onEvent)` holds one connection, returns `connected` / `stale`, dedupes by event id, reconnects with exponential backoff, and reopens the stream when `kinds` changes (the URL is fixed for the life of a connection, so a widened list needs a new one)

## Technical details

- Subscriptions and relay tasks are created INSIDE the response generator, not in the handler body
- An async generator closed without ever being iterated never runs its body, so a `finally` there can only undo setup that also happened there; setting up in the handler leaked a subscription per client that disconnected before the stream started

---

# LoRA Studio routes (session-only, no agent scope)

## API endpoints

### POST /api/loras/ingest

- Form field `url`, a `civitai.com` / `civitai.red` model page
- Answers `202` with the pending row and runs the download in a background task
- `400` for any other host or an unparseable URL

### GET /api/loras

- `{"loras": [...], "count": n}`, newest first
- Optional `?status=pending|downloading|ready|failed`

### GET /api/loras/{id}

- One row, `404` if unknown

### GET /api/loras/{id}/preview/{n}

- Serves stored preview image `n`
- Paths are re-checked against the archive root before the file is served

### DELETE /api/loras/{id}

- Removes the row, the safetensors file, and the LoRA directory
- Refuses with `400` if a stored path resolves outside the archive root rather than deleting it

### POST /api/loras/{id}/retry

- Re-runs a `failed` ingest
- The `failed → pending` transition is a single atomic UPDATE, so concurrent retries get one `202` and one `409`, never two download jobs in one directory

## Archive layout

- Files land under `models_root()/loras/<slug>/`
- `GET /api/models` excludes that subtree, so adapters never appear as loadable models

---

# What `GET /api/decisions/agent` returns (grant scoping)

## Grant shaping which decisions come back

### Global (null-project) grant

- **null-project decisions ONLY**

### Exactly one project grant

- That project's decisions, filtered in the store query

### Two or more projects

- Fetched by agent, then filtered in Python

### Limit interaction

- The global and single-project paths push the project filter into the store query, so the 500 limit applies AFTER scoping (issue #2194)
- The two-or-more-project path still fetches up to 500 rows for the agent and filters afterwards in Python, so an agent holding grants on several projects and carrying more than 500 decisions in total can still lose allowed-project rows to the limit
- Same shape as the original bug, narrower blast radius

---

# Config save and restore (`/api/config`, session-only)

## API endpoints

### GET /api/config

- `{"yaml": "<serialised AppConfig>"}`

### PUT /api/config

- Body: `{"yaml": "..."}`
- Optional `?validate_only=true` to check without saving
- Answers `400` with `details` when validation fails

### POST /api/restore

- Multipart `file`, restores a backup tarball into the data dir
- **The path is `/api/restore`, NOT `/api/settings/restore`**, even though the handler sits in `routes/settings.py` beside the `/api/settings/*` routes

## Important: both write paths REBUILD `AppConfig` field by field

- A field missing from either rebuild is silently dropped on the next save, wiping whatever the user had set
- This has now happened twice: `archive`, `archived_agents` and `github_app_id` (#2375) and `lora_ingest_proxy_url` (#2374)
- Adding a field to `AppConfig` means adding it at BOTH sites in this module
- `test_save_config_preserves_all_to_dict_keys` compares the whole `to_dict()` key set against what survives a round trip and fails if one is forgotten
- Never fix such a leak by removing the field from `to_dict()`: `save_config()` serialises from there, so that makes the setting unpersistable

---

# Agent memory mode (deploy + `PATCH /api/agents/{slug}/memory`, session-only)

## Memory mode values

| value | meaning |
|---|---|
| `both` | framework-native memory AND taOSmd (the default) |
| `framework` | the framework's own memory only |
| `taosmd` | taOSmd only |

## Key points

- `framework` is ADVISORY today, not enforced. The mode tells the agent runtime what to use; it does **not** yet stop the controller from involving taOSmd. A `framework`-mode deploy still registers the agent with taOSmd and still splices taOSmd rules into `AGENTS.md`. So a taOSmd outage can still block a `framework` deploy.

- `memory_mode` is OPTIONAL on `PATCH /api/agents/{slug}/memory` and omitting it leaves the stored value alone. Only `memory_plugin` is required.

- Agents deployed before this field existed are backfilled to `both` by `config.py` when the config loads, so an older agent record without the key reads as the default rather than as empty.

- `POST /api/agents/deploy` takes `memory_mode` on the body, defaulting to `both`. It is persisted on the agent record and injected into the agent's environment as `TAOS_MEMORY_MODE` at deploy time, so the runtime honours it without a second push.

- Deploy validates the pair before any side effect: an unknown `memory_mode` or `memory_plugin` answers `400` naming the valid set, and so does a contradictory pair such as `{"memory_plugin": "none", "memory_mode": "taosmd"}`.

---

# Cluster node revoke, block and unblock (admin-only)

## API endpoints

### POST /api/cluster/workers/{name}/revoke

- Kills the node's HMAC signing key
- Subsequent register and heartbeat requests are rejected
- The node may re-pair through the normal announce/confirm/claim flow to obtain a fresh key
- Answers `{"revoked": true, "changed": <bool>}`

### POST /api/cluster/workers/{name}/block

- Revokes the key AND refuses re-pairing until an admin unblocks
- The distinction from revoke: acts at the pairing gate, not merely at the auth gate
- So it cannot come back on its own

### POST /api/cluster/workers/{name}/unblock

- Clears the blocked flag only
- The old signing key stays dead, so the node still has to re-pair for a fresh one
- Unblock is permission to return, not restoration of access

## Common behaviour

- `404` when the node is absent from the PAIRING store (was never paired)
- `503` when the pairing store is unavailable, kept distinct from `404`
- Revoke and block mark the in-memory worker **offline immediately** so the scheduler stops routing tasks to it
- Blocked devices keep consuming a per-user slot: `list_for_user` returns rows where `revoked=0 OR blocked=1`, so a blocked device counts against `_MAX_DEVICES_PER_USER` until it is unblocked

---

# Answering a select decision with free text (`other_value`)

## `single_select`

- Send `other_value` and leave `value` empty
- Sending both is a `400` ("cannot combine value with other_value")
- The stored answer is the stripped `other_value`

## `multi_select`

- `value` must still be a list and **every element is still validated against the declared options**
- The free-text entry is appended, so the stored answer is `[*declared_values, other_value.strip()]`
- A non-list `value` is a `400`

## Note field

- When present it is appended to the text routed to the agent as `<answer> (note: <note>)`

## Without `other_value`

- The original strict validation is unchanged: the answer must be one of, or a subset of, the declared options
- A non-hashable or non-iterable value fails closed as `400` rather than `500`

## Two consequences

- **There is no per-decision opt-out.** No `allow_other` flag exists, so the free-text path is available on EVERY select decision
- **The agent path gained it too.** An agent holding `decisions_write` can record arbitrary free text where it was previously constrained to the declared options

---

# User resource sharing (share routes)

## API endpoints

### POST /api/shares

- Body: `{resource_type, resource_id, to_username, permission}`
- Share a resource with another user by username
- Resolves the target via AuthManager; self-share is rejected (400)
- Duplicate shares (same owner, resource, target, permission) are idempotent

### GET /api/shares?direction=out|in

- `out` (default) returns shares the user owns
- `in` returns shares where the user is the target

### POST /api/shares/{id}/accept

- Accept a pending share (target user only)
- Once accepted, the module-level helper `user_can_access()` returns True for that resource

### POST /api/shares/{id}/deny

- Deny a pending share (target user only)
- The share row is preserved with `status=denied` for audit

### DELETE /api/shares/{id}

- Revoke a share
- Owner or admin only (requires `require_owner_or_admin` against the share's `owner_user_id`)

---

# Routes Source Index

## Compile order

Run `python3 scripts/build-routes-doc.py` to compile these into `docs/routes.md`.

| File | Contents |
|---|---|
| `01-project-tasks.md` | Project tasks (kanban board) and `project_tasks` scope |
| `02-agent-api.md` | Agent API surface (scoped registry JWT) |
| `03-device-bearer.md` | Device bearer self-service (narrower passthrough) |
| `04-project-invite.md` | Project invite redeem route (link + PIN) |
| `05-os-events.md` | OS change-event stream (SSE) |
| `06-lora-studio.md` | LoRA Studio routes (session-only) |
| `07-decisions-return.md` | What `GET /api/decisions/agent` returns (grant scoping) |
| `08-config-save-restore.md` | Config save and restore (`/api/config`) |
| `09-agent-memory.md` | Agent memory mode (deploy + PATCH memory) |
| `10-cluster-admin.md` | Cluster node revoke, block and unblock (admin-only) |
| `11-select-decision.md` | Answering a select decision with free text (`other_value`) |
| `12-share-routes.md` | User resource sharing (share routes) |
