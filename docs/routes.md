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

### PATCH body semantics

`PATCH /api/projects/{pid}/tasks/{id}` writes exactly the fields sent and returns the stored task. Omitted = unchanged. `assignee_id`, `parent_task_id`, `element_id` accept `null` as a real clear (`element_id` also the legacy `"none"`). `null` elsewhere, an unknown key, or a read-only column (`id`, `created_by`, `claimed_by`) is a `422` — never a `200` echoing an unchanged task.

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

`PATCH /api/projects/{pid}/tasks/{tid}` — whitelisted fields (title, body, labels, priority), own-or-lead cards only. SEPARATE from `project_tasks`; plain project_tasks token gets 403. The whitelist keys on which fields the body SENDS, so `{"assignee_id": null}` is a 403 like any other assignee edit.

### canvas_read & canvas_write

`GET .../canvas/elements`, `POST|PATCH|DELETE .../canvas/elements/{id}` require `canvas_read` or `canvas_write` scope respectively.

### files_read & files_write

Files routes key on the project SLUG. `GET .../files/{path}`, `POST .../files/upload`, `DELETE .../files/{path}`.

### decisions_write

`POST /api/decisions` — raise a human-in-the-loop decision. `POST /api/decisions/{id}/answer/agent` — mirror an answer.

### a2a bus surface

`GET /api/a2a/bus/channels`, `GET /api/a2a/bus/messages`, `GET|POST /api/a2a/bus/stream`. `a2a_receive` cannot post; `a2a_send` isn't thereby a reader.

### CONSENT KEY surface

`GET /v1/models` and `POST /v1/chat/completions` are reachable without a session using a CONSENT KEY. No key, no resolution, OpenAI-shaped 401 otherwise. Only those two exact method+path pairs pass the middleware.

---

# Device bearer self-service (second, narrower passthrough)

## Properties

- Device prefix matching: only tokens carrying `taosdev_` match; previously any bearer matched, shadowing valid sessions (401 for a logged-in user's unrelated `Authorization` header)
- Allowlist is method-and-path anchored: `GET /api/devices`, `DELETE /api/devices/{id}`, `POST /api/decisions` are deliberately NOT on it (session-only)
- Device identity always comes from the verified bearer, never the path or body; a device is never admin

## Auth model

- Caller sends `Authorization: Bearer <scoped_token>` (issued at `POST /api/devices/register`); browser sessions and agent JWTs are not accepted
- The path is in `EXEMPT_PATHS` (`tinyagentos/auth_middleware.py`): middleware passes `user_id=None`, `current_user_or_device` resolves the device
- CSRF: registered on the router (`dependencies=_csrf`) so future unsafe-method routes inherit the double-submit check; GET is exempt as safe

## Coverage

- `agent_chat` destinations resolve via the agent registry (exact canonical_id, then a slug lookup bounded to the `-YYYYMMDD-HHMMSS` tail); an agent with no registry row resolves nothing and its DM is omitted

## Response shape

```json
{"destinations": [
  {"kind": "library", "id": "library", "label": "Library"},
  {"kind": "project_files", "id": "<project-slug>", "label": "<project name>"},
  {"kind": "agent_chat", "id": "<agent-slug>", "label": "<display name>"}
]}
```

---

# Project invite redeem route (link + PIN)

## Endpoints

### POST /api/projects/invites/redeem

Body: `{invite_id, pin, harness, label?}`

- Verifies the PIN (wrong / expired / attempt-capped → 403; already redeemed / revoked → 409)
- Derives the agent handle `{project_slug}-{harness}[-{label}]`, de-duped against active registry agents in the project
- Auto-approves via `approve_request_record` (decided_by = the invite's creator), or leaves the request pending (manual mode)
- Returns a connection bundle plus `{request_id, agent_handle, poll_path}`
- `project_tasks` is force-included so a successful redeem always yields a project member

### GET /i/{invite_id}

Content-negotiated advert: `Accept: application/json` → the redeem contract (`{method, path, fields}`); browser → a minimal HTML page. No PIN check here; it only advertises the contract.

## Connection bundle

- `controller.endpoints` — non-loopback LAN IPv4s (priority ordered, operator override first) and the mesh (Tailscale) node IP when joined; no relay in Phase 1
- `apis` — agent-JWT-reachable surface, scoped exactly to the granted scopes (mirrors the middleware allowlist)
- `delivery` — timed-check contract (`poll_path`, `stream_path`, `check_interval_secs`, `cursor: ts`, `filter: mentions+project`)
- `onboarding` + `guide_markdown` — personalized capability guide (repo link, agent manual links, scoped Projects/Canvas summary, the A2A authenticated-proxy contract)

See `docs/design/external-agent-project-invite.md` (issue #1780); canvas routes advertise only when that scope was granted.

---

# OS change-event stream (`GET /api/os/events`, session-only)

## SSE stream characteristics

- `?kinds=a,b,c` — comma-separated allowlist of event kinds
- Omitted, empty, or naming no kind at all (`?kinds=`, `?kinds=%20`, `?kinds=,`) means every kind (empty allowlist = no filter, not silence)
- Filtering happens as events enter the per-connection buffer, so an unrequested kind can never evict one the subscriber asked for
- At most 256 events are buffered per connection; past that the OLDEST is dropped and the client gets `{"kind": "events.lagged", "dropped": N}` as a cue to refetch
- A `:keepalive` comment frame every 10 s keeps proxies from closing an idle stream
- Frames carry **no** SSE `id:` line; resume is best-effort via the EventBus replay buffer (last 32 events per channel, delivered on subscribe)
- The payload never crosses the wire: `id` is just the trace id, so a subscriber refetches to learn what changed

## Desktop integration

- `desktop/src/hooks/use-os-events.ts`: `useOsEvents(kinds, onEvent)` holds one connection, returns `connected` / `stale`, dedupes by event id, reconnects with backoff, and reopens the stream when `kinds` changes

## Technical details

- Subscriptions and relay tasks are created INSIDE the response generator, not the handler body: a generator closed before iteration never runs its `finally`; handler-side setup leaked a subscription per client that disconnected before the stream started

---

# LoRA Studio routes (session-only, no agent scope)

## API endpoints

### POST /api/loras/ingest

- Form field `url`, a `civitai.com` / `civitai.red` model page
- Answers `202` with the pending row; the download runs in a background task
- `400` for any other host or an unparseable URL

### GET /api/loras

- `{"loras": [...], "count": n}`, newest first
- Optional `?status=pending|downloading|ready|failed`

### GET /api/loras/{id}

- One row; `404` if unknown

### GET /api/loras/{id}/preview/{n}

- Serves stored preview image `n`
- Path re-checked against the archive root before serving

### DELETE /api/loras/{id}

- Removes the row, the safetensors file and the LoRA directory
- `400` rather than a delete if a stored path resolves outside the archive root

### POST /api/loras/{id}/retry

- Re-runs a `failed` ingest
- The `failed → pending` transition is one atomic UPDATE: concurrent retries get one `202` and one `409`, never two download jobs in one directory

## Archive layout

- Files land under `models_root()/loras/<slug>/`
- `GET /api/models` excludes that subtree, so adapters never appear as loadable models

---

# What `GET /api/decisions/agent` returns (grant scoping)

## Grant shaping which decisions come back

- **Global (null-project) grant**: null-project decisions ONLY
- **Exactly one project grant**: that project's decisions, filtered in the store query
- **Two or more projects**: fetched by agent, filtered in Python

### Limit interaction

- The global and single-project paths push the project filter into the store query, so the 500 limit applies AFTER scoping (issue #2194)
- The two-or-more-project path still fetches up to 500 rows then filters in Python, so an agent with several project grants and more than 500 decisions in total can still lose allowed-project rows to the limit (same shape as the original bug, narrower blast radius)

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
- Upload capped at 64 MB, refused with `413` while the body is still arriving (`tinyagentos/middleware/upload_body_limit.py`, since FastAPI spools a multipart file before the handler runs); the tarball goes through `tinyagentos/safe_archive.py`, so over the shared bomb caps (256 MB declared uncompressed, 64 MB per member, 10000 members) or carrying a member the path-safe tar filter rejects, the restore answers `400` and writes nothing

## Important: both write paths REBUILD `AppConfig` field by field

- A field missing from either rebuild is silently dropped on the next save, wiping whatever the user had set
- Has happened twice already: `archive`, `archived_agents` and `github_app_id` (#2375) and `lora_ingest_proxy_url` (#2374)
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

- `framework` is ADVISORY today, not enforced: it tells the agent runtime what to use but does **not** stop the controller from involving taOSmd. A `framework`-mode deploy still registers with taOSmd and splices taOSmd rules into `AGENTS.md`, so a taOSmd outage can still block it.
- `memory_mode` is OPTIONAL on `PATCH /api/agents/{slug}/memory`; omitting it leaves the stored value alone. Only `memory_plugin` is required.
- Agents deployed before this field existed are backfilled to `both` by `config.py` on config load, so an older record reads as the default rather than as empty.
- `POST /api/agents/deploy` takes `memory_mode` (default `both`), persisted on the agent record and injected into the agent's environment as `TAOS_MEMORY_MODE` at deploy time.
- Deploy validates before any side effect: an unknown `memory_mode` or `memory_plugin` answers `400` naming the valid set, as does a contradictory pair such as `{"memory_plugin": "none", "memory_mode": "taosmd"}`.

---

# Cluster node revoke, block, unblock and fleet mutations (admin-only)

## API endpoints

### POST /api/cluster/workers/{name}/revoke

- Kills the node's HMAC signing key; register and heartbeat are rejected until it re-pairs (announce/confirm/claim) for a fresh key
- Answers `{"revoked": true, "changed": <bool>}`

### POST /api/cluster/workers/{name}/block

- Revokes the key AND refuses re-pairing until an admin unblocks (acts at the pairing gate, not the auth gate)

### POST /api/cluster/workers/{name}/unblock

- Clears the blocked flag only; the old signing key stays dead, so the node still has to re-pair

### Other fleet mutations (same admin gate)

`DELETE /api/cluster/workers/{name}`, `POST .../{name}/deploy`, `POST .../{name}/remote`, `POST /api/cluster/move`, `/route`, `/promote-archived`: `403 {"detail": "forbidden"}` unless admin session or host local token. Worker-facing paths (heartbeat, pairing, leases, capabilities) keep their HMAC / possession gates.

## Common behaviour

- `404` when the node is absent from the PAIRING store; `503` when the pairing store is unavailable
- Revoke and block mark the in-memory worker **offline immediately** so the scheduler stops routing to it
- Blocked devices keep consuming a per-user slot (`list_for_user` returns `revoked=0 OR blocked=1`) until unblocked

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

- When present, appended to the text routed to the agent as `<answer> (note: <note>)`

## Without `other_value`

- Strict validation is unchanged: the answer must be one of, or a subset of, the declared options
- A non-hashable or non-iterable value fails closed as `400` rather than `500`

## Two consequences

- **No per-decision opt-out.** No `allow_other` flag exists; the free-text path is available on EVERY select decision
- **The agent path gained it too.** An agent holding `decisions_write` can now record arbitrary free text, not only the declared options

---

# User resource sharing (share routes)

## API endpoints

### POST /api/shares

- Body: `{resource_type, resource_id, to_username, permission}`
- Shares a resource with another user by username (resolved via AuthManager); self-share is `400`
- Duplicate shares (same owner, resource, target, permission) are idempotent

### GET /api/shares?direction=out|in

- `out` (default): shares the user owns; `in`: shares where the user is the target

### POST /api/shares/{id}/accept

- Accept a pending share (target user only); afterwards `user_can_access()` returns True for that resource

### POST /api/shares/{id}/deny

- Deny a pending share (target user only); the row is kept with `status=denied` for audit

### DELETE /api/shares/{id}

- Revoke a share; owner or admin only (`require_owner_or_admin` against the share's `owner_user_id`)

---

# Admin gates on global resources

A session alone doesn't authorize these: non-admin members get `403`; the host local token (`taosctl`, agents) passes. Single-user installs are unaffected.

| Router | Gated | Open / owner-scoped |
|---|---|---|
| secrets | list, get, add, update, delete, `categories` | `GET /api/secrets/agent/{agent}`: the agent's owner (registry `user_id`) or admin |
| system | `restart/prepare`, `ai-stack/restart`, non-loopback `prepare-shutdown` | loopback `prepare-shutdown`, `restart/status`, `hardware/refresh` |
| providers | create, patch, delete, `start`, `stop` | `GET /api/providers` (model pickers) with `api_key` stripped for non-admins |
| mcp | `start`/`stop`/`restart`, uninstall, `config` PUT, `env`, permission attach/detach, `/api/mcp/call` | list, logs, capabilities, permissions list, `config` GET |
| agent-model-keys | `POST /api/agent-model-keys` mints only for agents the caller owns (admin: any) | |

---

# Agent desktop lifecycle

## Routes

Under `/api/agents/{agent_name}/desktop/`:

| Method | Path | Purpose |
|---|---|---|
| `POST` | `install` | Install XFCE + x11vnc |
| `POST` | `start` | Start desktop + VNC |
| `POST` | `stop` | Stop desktop |
| `GET` | `status` | Runtime state |

## Key points

- On demand, per agent, retryable. Owner or admin only.
- Start returns a one-shot VNC password, mode-600 file not argv; a secret
  left behind fails the start (no password).
- `status` 500s, records the error, keeps state; `running` is `null` then,
  not `false`.

---

# Routes Source Index

## Compile order

Run `python3 scripts/build-routes-doc.py` to compile these into `docs/routes.md`. Source files, in order: `01-project-tasks.md`, `02-agent-api.md`, `03-device-bearer.md`, `04-project-invite.md`, `05-os-events.md`, `06-lora-studio.md`, `07-decisions-return.md`, `08-config-save-restore.md`, `09-agent-memory.md`, `10-cluster-admin.md`, `11-select-decision.md`, `12-share-routes.md`, `13-admin-gates.md`, `14-agent-desktop.md`.
