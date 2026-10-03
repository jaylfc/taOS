<!-- GENERATED from docs/routes.d/ by scripts/build-routes-doc.py. Edit the source files, not this file. -->

# Project tasks (kanban board)

## Project tasks

Access the kanban board for a project. Granting `project_tasks` also makes the agent a project member.

### API endpoints

- `GET /api/projects/{pid}/tasks` — list tasks
- `GET /api/projects/{pid}/tasks/ready` — list ready
- `GET /api/projects/{pid}/tasks/{id}` — get task
- `GET /api/projects/{pid}/tasks/{id}/comments` — list comments
- `POST /api/projects/{pid}/tasks/{id}/claim` — claim (LEAD-only)
- `POST /api/projects/{pid}/tasks/{id}/release` — release claimed
- `POST /api/projects/{pid}/tasks/{id}/close` — close
- `POST /api/projects/{pid}/tasks/{id}/reopen` — reopen
- `GET /api/projects/tasks/{id}/context` — get context

### PATCH body semantics

`PATCH /api/projects/{pid}/tasks/{id}` writes exactly fields sent, returns stored task. Omitted = unchanged. `assignee_id`, `parent_task_id`, `element_id` accept `null` as real clear (`element_id` also legacy `"none"`). `null` elsewhere, unknown key, or read-only (`id`, `created_by`, `claimed_by`) → `422`, never `200` echo.

### LEAD-only extensions

- `POST .../tasks/{id}/claimable` — add/remove `claimable` label (LEAD-only)
- `POST .../tasks/{id}/unquarantine` — return quarantined card to open pool (LEAD-only)

---

# Agent API surface (scoped registry JWT)

## Scoped allowlist

Agents authenticate with registry JWT (`Authorization: Bearer`) and reach exactly routes their granted SCOPES allow, nothing else.

### project_tasks (the kanban board)

Granting `project_tasks` also makes the agent a project member.

### project_tasks_create

`POST /api/projects/{pid}/tasks` — author new cards. SEPARATE from `project_tasks`; off by default.

### project_tasks_update

`PATCH /api/projects/{pid}/tasks/{tid}` — whitelisted fields (title, body, labels, priority), own-or-lead only. SEPARATE from `project_tasks`; plain token gets 403. Whitelist keys on fields body SENDS, so `{"assignee_id": null}` is 403.

### canvas_read & canvas_write

`GET .../canvas/elements`, `POST|PATCH|DELETE .../canvas/elements/{id}` require `canvas_read`/`canvas_write` respectively.

### files_read & files_write

Files routes key on project SLUG. `GET .../files/{path}`, `POST .../files/upload`, `DELETE .../files/{path}`.

### decisions_write

`POST /api/decisions` — raise human-in-the-loop decision. `POST /api/decisions/{id}/answer/agent` — mirror answer.

### a2a bus surface

`GET /api/a2a/bus/channels`, `GET /api/a2a/bus/messages`, `GET|POST /api/a2a/bus/stream`. `a2a_receive` cannot post; `a2a_send` isn't thereby a reader.

### CONSENT KEY surface

`GET /v1/models` and `POST /v1/chat/completions` reachable without session via CONSENT KEY. No key = no resolution, OpenAI-shaped 401. Only those two exact method+path pairs pass middleware.

---

# Device bearer self-service (second, narrower passthrough)

## Properties

- Device prefix matching: only `taosdev_` tokens match; previously any bearer matched, shadowing valid sessions
- Allowlist is method+path anchored: `GET /api/devices`, `DELETE /api/devices/{id}`, `POST /api/decisions` NOT on it (session-only)
- Device identity from verified bearer only, never path/body; device never admin

## Auth model

- Caller sends `Authorization: Bearer <scoped_token>` (issued at `POST /api/devices/register`); browser sessions/agent JWTs rejected
- Path in `EXEMPT_PATHS` (`auth_middleware.py`): middleware passes `user_id=None`, `current_user_or_device` resolves device
- CSRF on router (`dependencies=_csrf`) so future unsafe routes inherit double-submit; GET exempt as safe

## Coverage

- `agent_chat` destinations resolve via agent registry (exact canonical_id, then slug lookup bounded to `-YYYYMMDD-HHMMSS` tail); no registry row = nothing resolved, DM omitted

## Response shape

`{"destinations": [{"kind": "library", "id": "library", "label": "Library"}, {"kind": "project_files", "id": "<project-slug>", "label": "<project name>"}, {"kind": "agent_chat", "id": "<agent-slug>", "label": "<display name>"}]}`

---

# Project invite redeem route (link + PIN)

## Endpoints

### POST /api/projects/invites/redeem

Body: `{invite_id, pin, harness, label?}`

- Verifies PIN (wrong/expired/attempt-capped → 403; redeemed/revoked → 409)
- Derives handle `{project_slug}-{harness}[-{label}]`, de-duped vs active registry agents
- Auto-approves via `approve_request_record` (decided_by = invite creator), or leaves pending (manual)
- Returns connection bundle + `{request_id, agent_handle, poll_path}`
- `project_tasks` force-included → successful redeem = project member

### GET /i/{invite_id}

Content-negotiated: `Accept: application/json` → redeem contract (`{method, path, fields}`); browser → minimal HTML. No PIN check; only advertises contract.

## Connection bundle

- `controller.endpoints` — non-loopback LAN IPv4s (priority, override first) + mesh (Tailscale) IP when joined; optional relay when `TAOS_CONTROLLER_RELAY_URL` is `https://`
- `apis` — agent-JWT surface, scoped to granted scopes (mirrors middleware allowlist)
- `delivery` — timed-check contract (`poll_path`, `stream_path`, `check_interval_secs`, `cursor: ts`, `filter: mentions+project`)
- `onboarding` + `guide_markdown` — capability guide (repo/manual links, scoped Projects/Canvas, A2A proxy, memory + timed checks). `harness=grok` adds secure-form token storage, onboarding polling, shared-account warning.

See `docs/design/external-agent-project-invite.md` (#1780); canvas routes only when scope granted.

## Override addresses advertised over HTTP

Operator sets `TAOS_CONTROLLER_CALLBACK_HOST`. Advertised over **HTTP**:

- Tailscale CGNAT `100.64.0.0/10` (e.g. `100.78.225.80`)
- Tailscale ULA `fd7a:115c:a1e0::/48`
- Private LAN (RFC 1918: `10/8`, `172.16/12`, `192.168/16`)
- Loopback (`127.0.0.1`, `::1`, `localhost`)
- Hostnames ending `.local`, `.lan`, `.home.arpa`, `.ts.net` (MagicDNS)
- Single-label (e.g. `taos`, `controller`)

**NOT advertised** (omitted with warning): public IPs, public hostnames (e.g. `controller.example.com`), hostnames not matching trusted suffixes.

Full URL override (e.g. `http://192.168.1.5:6969`) uses scheme/port as-is; LAN IP enum deduplicates vs parsed hostname.

**Relay** (`TAOS_CONTROLLER_RELAY_URL`): `https://` only. HTTP relay URLs omitted with warning.

---

# OS change-event stream (`GET /api/os/events`, session-only)

## SSE stream characteristics

- `?kinds=a,b,c` — comma-separated allowlist of event kinds
- Omitted/empty/no kind = every kind (empty allowlist = no filter, not silence)
- Filtering at buffer entry: unrequested kind never evicts requested
- Max 256 events/conn; oldest dropped → `{"kind": "events.lagged", "dropped": N}` cues refetch
- `:keepalive` comment every 10s prevents proxy close on idle
- No SSE `id:` line; resume via EventBus replay (last 32/channel, on subscribe)
- Payload never on wire: `id` is trace id, subscriber refetches to learn changes

## Desktop integration

- `desktop/src/hooks/use-os-events.ts`: `useOsEvents(kinds, onEvent)` holds one conn, returns `connected`/`stale`, dedupes by event id, reconnects with backoff, reopens on `kinds` change

## Technical details

- Subscriptions/relay tasks created INSIDE response generator, not handler: generator closed before iteration never runs `finally`; handler-side setup leaked sub per client disconnecting before stream start

---

# LoRA Studio routes (session-only, no agent scope)

## API endpoints

### POST /api/loras/ingest

- Form `url`: `civitai.com` / `civitai.red` model page
- `202` with pending row; download in background
- `400` for other host/unparseable URL

### GET /api/loras

- `{"loras": [...], "count": n}`, newest first
- Optional `?status=pending|downloading|ready|failed`

### GET /api/loras/{id}

- One row; `404` if unknown

### GET /api/loras/{id}/preview/{n}

- Serves stored preview `n`
- Path re-checked against archive root before serving

### DELETE /api/loras/{id}

- Removes row, safetensors file, LoRA directory
- `400` if stored path resolves outside archive root

### POST /api/loras/{id}/retry

- Re-runs `failed` ingest
- `failed → pending` is atomic UPDATE: concurrent retries get one `202`, one `409`, never two jobs in one dir

## Archive layout

- Files under `models_root()/loras/<slug>/`
- `GET /api/models` excludes that subtree, adapters never appear as loadable models

---

# What `GET /api/decisions/agent` returns (grant scoping)

## Grant shaping which decisions come back

- **Global (null-project) grant**: null-project decisions ONLY
- **Exactly one project grant**: that project's decisions, filtered in store query
- **Two or more projects**: fetched by agent, filtered in Python

### Limit interaction

- Global/single-project: project filter pushed into store query, 500 limit applies AFTER scoping (#2194)
- Two-or-more: fetches up to 500 then filters in Python, so agent with several grants and >500 total decisions can lose allowed-project rows to limit (same shape as original bug, narrower blast radius)

---

# Config save and restore (`/api/config`, session-only)

## API endpoints

### GET /api/config

- `{"yaml": "<serialised AppConfig>"}`

### PUT /api/config

- Body: `{"yaml": "..."}`
- Optional `?validate_only=true` to check without saving
- `400` with `details` on validation failure

### POST /api/restore

- Multipart `file`, restores backup tarball to data dir
- **Path is `/api/restore`, NOT `/api/settings/restore`** (handler in `routes/settings.py`)
- Upload capped 64 MB, refused `413` while body arriving (`upload_body_limit.py`); tarball via `safe_archive.py` — over bomb caps (256 MB declared, 64 MB/member, 10000 members) or rejected by path-safe tar filter → `400`, writes nothing

## Important: both write paths REBUILD `AppConfig` field by field

- Missing field in either rebuild = silently dropped on next save, wiping user setting
- Happened twice: `archive`, `archived_agents`, `github_app_id` (#2375) and `lora_ingest_proxy_url` (#2374)
- Adding field to `AppConfig` = add at BOTH sites in this module
- `test_save_config_preserves_all_to_dict_keys` compares `to_dict()` key set vs round-trip, fails if forgotten
- Never fix leak by removing from `to_dict()`: `save_config()` serialises from there, making setting unpersistable

---

# Agent memory mode (deploy + `PATCH /api/agents/{slug}/memory`, session-only)

## Memory mode values

| value | meaning |
|---|---|
| `both` | framework-native + taOSmd (default) |
| `framework` | framework's own memory only |
| `taosmd` | taOSmd only |

## Key points

- `framework` is ADVISORY, not enforced: tells runtime what to use but doesn't stop controller from involving taOSmd. `framework`-mode deploy still registers with taOSmd, splices rules into `AGENTS.md`, so taOSmd outage can still block.
- `memory_mode` OPTIONAL on `PATCH /api/agents/{slug}/memory`; omit = leave stored value. Only `memory_plugin` required.
- Pre-field agents backfilled to `both` by `config.py` on load.
- `POST /api/agents/deploy` takes `memory_mode` (default `both`), persisted on record, injected as `TAOS_MEMORY_MODE` at deploy.
- Deploy validates first: unknown `memory_mode`/`memory_plugin` → `400` naming valid set; contradictory pair (e.g. `{"memory_plugin": "none", "memory_mode": "taosmd"}`) → `400`.

---

# Cluster node revoke, block, unblock and fleet mutations (admin-only)

## API endpoints

### POST /api/cluster/workers/{name}/revoke

- Kills HMAC signing key; register/heartbeat rejected until re-pair (announce/confirm/claim) for fresh key
- Answers `{"revoked": true, "changed": <bool>}`

### POST /api/cluster/workers/{name}/block

- Revokes key AND refuses re-pair until admin unblocks (pairing gate, not auth gate)

### POST /api/cluster/workers/{name}/unblock

- Clears blocked flag only; old key stays dead, node must re-pair

### Other fleet mutations (same admin gate)

`DELETE /api/cluster/workers/{name}`, `POST .../{name}/deploy`, `POST .../{name}/remote`, `POST /api/cluster/move`, `/route`, `/promote-archived`: `403 {"detail": "forbidden"}` unless admin session or host local token. Worker-facing (heartbeat, pairing, leases, capabilities) keep HMAC/possession gates.

## Common behaviour

- `404` node absent from PAIRING store; `503` pairing store unavailable
- Revoke/block mark in-memory worker **offline immediately** so scheduler stops routing
- Blocked devices consume per-user slot (`list_for_user` → `revoked=0 OR blocked=1`) until unblocked

---

# Answering a select decision with free text (`other_value`)

## `single_select`

- Send `other_value`, leave `value` empty
- Both → `400` ("cannot combine value with other_value")
- Stored answer = stripped `other_value`

## `multi_select`

- `value` must be list, every element validated against declared options
- Free-text appended: stored = `[*declared_values, other_value.strip()]`
- Non-list `value` → `400`

## Note field

- When present, appended to text routed to agent as `<answer> (note: <note>)`

## Without `other_value`

- Strict validation unchanged: answer must be one of/subset of declared options
- Non-hashable/non-iterable → `400` (fails closed, not `500`)

## Two consequences

- **No per-decision opt-out.** No `allow_other` flag; free-text path on EVERY select decision
- **Agent path gained it too.** Agent with `decisions_write` can record arbitrary free text, not only declared options

---

# User resource sharing (share routes)

## API endpoints

### POST /api/shares

- Body: `{resource_type, resource_id, to_username, permission}`
- Shares resource with user by username (resolved via AuthManager); self-share `400`
- Duplicate shares (same owner, resource, target, permission) idempotent

### GET /api/shares?direction=out|in

- `out` (default): shares user owns; `in`: shares where user is target

### POST /api/shares/{id}/accept

- Accept pending share (target only); then `user_can_access()` returns True

### POST /api/shares/{id}/deny

- Deny pending share (target only); row kept with `status=denied` for audit

### DELETE /api/shares/{id}

- Revoke share; owner or admin only (`require_owner_or_admin` vs share's `owner_user_id`)

---

# Admin gates on global resources

Session alone doesn't authorize: non-admin members get `403`; host local token (`taosctl`, agents) passes. Single-user installs unaffected.

| Router | Gated | Open / owner-scoped |
|---|---|---|
| secrets | list, get, add, update, delete, `categories` | `GET /api/secrets/agent/{agent}`: agent's owner (registry `user_id`) or admin |
| system | `restart/prepare`, `ai-stack/restart`, non-loopback `prepare-shutdown` | loopback `prepare-shutdown`, `restart/status`, `hardware/refresh` |
| providers | create, patch, delete, `start`, `stop` | `GET /api/providers` (model pickers) with `api_key` stripped for non-admins |
| mcp | `start`/`stop`/`restart`, uninstall, `config` PUT, `env`, permission attach/detach, `/api/mcp/call` | list, logs, capabilities, permissions list, `config` GET |
| agent-model-keys | `POST /api/agent-model-keys` mints only for agents caller owns (admin: any) | |

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
- Start returns one-shot VNC password, mode-600 file not argv; secret left behind fails start.
- `status` 500s, records error, keeps state; `running` is `null` then, not `false`.

---

# Routes Source Index

## Compile order

Run `python3 scripts/build-routes-doc.py` to compile into `docs/routes.md`. Sources in order: `01-project-tasks.md`, `02-agent-api.md`, `03-devive-bearer.md`, `04-project-invite.md`, `05-os-events.md`, `06-lora-studio.md`, `07-decisions-return.md`, `08-config-save-restore.md`, `09-agent-memory.md`, `10-cluster-admin.md`, `11-select-decision.md`, `12-share-routes.md`, `13-admin-gates.md`, `14-agent-desktop.md`, `15-decision-note.md`.

---

# Adding a note to a decision

## `POST /api/decisions/{decision_id}/note`

Body:
```json
{"text": "string (required, non-empty after strip)", "source": "in_app"}
```

- `text` REQUIRED, non-empty after strip → `400`. No default.
- Ownership: caller must own decision or be admin → `404`. Same rule as `answer_decision`.
- Device bearer MAY post note on ANY decision INCLUDING gate-kind. Note carries no grant, so phone notification restriction on `answer_decision` doesn't apply.
- Does NOT change `status`, `answer`, `answered_at`.
- Allowed on answered/superseded decisions (note = commentary, not state transition).
- Returns updated decision with `notes` (oldest first).

## Response

Updated decision dict, same shape as `GET /api/decisions/{id}`, with `notes` appended.

## Live update

Publishes `decision.note` on owner's `user:<id>` channel for live refresh.

---

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

---

# Device API v1

Single source of truth for the firmware repo. All routes under `/api/devices`
are bearer-authenticated (not session-authenticated).

## Pairing

### POST /api/devices/pair-requests

**Scope:** public (no session/device bearer). Opaque `pair_request_id` is capability for polling/Decision.

**Length caps:** `platform` required (ios/android/watchos/wearos/embedded/linux/windows/macos; else 400); `display_name` optional max 120; `push_token` optional max 4096.

**Request:** `{"platform": "ios", "display_name": "My Phone", "push_token": "apns-token"}`

**Response 200:** `{"pair_request_id": "uuid", "verify_code": "123456", "server_cert_fingerprint": "ab:cd:ef:..."}`

`server_cert_fingerprint` = SHA-256 colon-hex of controller TLS cert (DER). Cross-check only: device MUST display/pin fingerprint from own handshake, abort with `pair_fingerprint_mismatch` if differs.

**Errors:** `400` invalid platform/too large; `409` no admin; `429` too many pending.

### GET /api/devices/pair-requests/{pair_request_id}

**Scope:** public (capability = path param).

**Pending:** `{"id": "uuid", "status": "pending", "platform": "ios", "display_name": "My Phone", "push_token": "apns-token", "verify_code": null, "device": null, "scoped_token": null, "created_at": "...", "expires_at": "..."}`

`verify_code` only on POST creation; never on poll/Decision. Decision includes `server_cert_fingerprint` in metadata.

**Accepted:** `{"status": "accepted", "device": {"device_id": "uuid", "platform": "ios", "display_name": "My Phone", "user_id": "admin-uuid", "registered_at": 1727640000}, "scoped_token": "taosdev_...", "created_at": "...", "expires_at": "..."}`

`scoped_token` one-time: only on first accepted poll.

**Denied/expired:** `{"status": "denied", "device": null, "scoped_token": null}`

**Error:** `404` unknown `pair_request_id`.

## TLS listener

Embedded (`platform: embedded`) MUST use TLS on **6974** (`TAOS_DEVICE_TLS_PORT`). Plain HTTP 6969 refuses embedded bearers: `{"error": "device_tls_required"}`.

Phones/watches/legacy stay on plain HTTP 6969.

Controller generates self-signed cert on first boot, persists to data dir (`device_tls.crt/key`, 0600). Fingerprint survives restarts; no auto-rotation in v1; re-pair to change pin.

### Device pinning rule

1. Device opens TLS to `:<TAOS_DEVICE_TLS_PORT>`, computes SHA-256 fingerprint of server cert.
2. Displays fingerprint for manual verification.
3. Includes POST response `server_cert_fingerprint` in same display.
4. If differ, device MUST abort locally with `pair_fingerprint_mismatch` (device-side; server never returns this).

`server_cert_fingerprint` in POST is server-side cross-check only; authoritative value is device's handshake computation.

## Errors

| HTTP | Detail | Meaning |
|---|---|---|
| 400 | `invalid_platform` | Unknown `platform`. |
| 400 | `pair_request_too_large` | Body exceeds cap. |
| 403 | `device_tls_required` | Embedded bearer on plain HTTP. |
| 404 | `pair_request_not_found` | Unknown `pair_request_id`. |
| 409 | `no_admin_for_pairing` | No admin user. |
| 409 | `pair_request_expired` | Approval after TTL. |
| 409 | `pair_request_already_accepted` | Duplicate approve. |
| 429 | `pair_request_pending_cap_exceeded` | Too many open. |
