# OpenClaw Integration Reference

**Status:** Protocol/install/config research (§1–§5) is current reference material.
§6 mapping table and §7 roadmap describe the **historical fork-bridge design** — the live
implementation uses upstream npm + ACP instead. See the implementation summary before reading §6–§7.
**Last updated:** 2026-04-16 (research); 2026-06-04 (implementation summary added)

---

## Implementation summary (as-built, 2026-06-04)

The fork/bridge design documented in §6–§7 was superseded before it shipped. The live
implementation is:

- **Install:** `npm install -g openclaw@latest` (upstream npm, no fork). See
  `app-catalog/agents/openclaw/scripts/install.sh`.
- **Runtime:** taOS drives each agent turn **in** via ACP — it runs
  `openclaw acp --session agent:main:main` inside the container using `incus exec` and
  streams replies back. See `tinyagentos/openclaw_acp_runtime.py`.
- **No fork.** `jaylfc/openclaw` is archived. `src/taos-bridge.ts` was never shipped.
  Do not install `github:jaylfc/openclaw#<sha>` — it no longer exists.
- **Bridge SSE endpoints survive** (`GET /api/openclaw/sessions/{agent}/events`,
  `POST /api/openclaw/sessions/{agent}/reply`, `GET /api/openclaw/bootstrap`) — they
  live in `tinyagentos/routes/openclaw.py` and are reused by the Hermes bridge adapter
  (`tinyagentos/scripts/install_hermes.sh`). They are not dead code.
- The gateway port 18789 is reserved for operator tooling (config reload, log tailing)
  and is not used by taOS for normal chat turns.

§6 and §7 are retained as historical design reference only. Do not implement them.

---

## What this doc is for

This is the team's primary reference for the OpenClaw gateway protocol, installation and
runtime behaviour on Linux/arm64, the full configuration schema, the extension model, and
known limitations. §6–§7 document the original fork-bridge design and are kept for
historical context; they do not describe the live implementation.

---

## Table of contents

1. [Gateway protocol](#1-gateway-protocol)
2. [Installation and runtime](#2-installation-and-runtime)
3. [Configuration](#3-configuration)
4. [Extension model](#4-extension-model)
5. [Known limitations and feature gaps](#5-known-limitations-and-feature-gaps)
6. [taOS integration mapping](#6-taos-integration-mapping)
7. [MVP-to-full roadmap](#7-mvp-to-full-roadmap)

---

## 1. Gateway protocol

**Primary sources:**
- `docs/gateway/protocol.md` in the openclaw repo (fetched raw)
- `src/gateway/protocol/schema/frames.ts`
- `src/gateway/protocol/schema/logs-chat.ts`
- `src/gateway/protocol/schema/sessions.ts`
- `src/gateway/protocol/schema/exec-approvals.ts`
- `src/gateway/protocol/schema/nodes.ts`
- `src/gateway/protocol/schema/snapshot.ts`
- `src/gateway/protocol/schema/protocol-schemas.ts`
- `src/gateway/client.ts`
- `src/gateway/server-constants.ts`
- `src/gateway/handshake-timeouts.ts`

### 1.1 Transport

OpenClaw's gateway is a **WebSocket server** listening on TCP port 18789 by default. All frames are JSON text frames — no binary encoding. Every client (CLI, web UI, macOS app, iOS/Android nodes, taOS chat router) connects via the same WS endpoint and declares its role and scopes during the handshake. There is no separate REST API for the control plane; the WS gateway is the single control plane and node transport.

Protocol version at time of writing: **`PROTOCOL_VERSION = 3`** (defined in `src/gateway/protocol/schema/protocol-schemas.ts`).

TLS is supported. Clients may optionally pin the gateway certificate fingerprint via `gateway.remote.tlsFingerprint` config or CLI `--tls-fingerprint`. When a `wss://` endpoint is used with a pinned fingerprint, device-token auto-promotion on `AUTH_TOKEN_MISMATCH` retry is enabled (see §1.5).

### 1.2 Frame shapes

All WS messages are one of three frame types discriminated by the `type` field:

```json
// Request (client → gateway)
{ "type": "req", "id": "<uuid>", "method": "<method-name>", "params": { ... } }

// Response (gateway → client, correlates to a req by id)
{ "type": "res", "id": "<uuid>", "ok": true, "payload": { ... } }
{ "type": "res", "id": "<uuid>", "ok": false, "error": { "code": "...", "message": "...", "details": { ... } } }

// Event (gateway → client, unsolicited or subscription-driven)
{ "type": "event", "event": "<event-name>", "payload": { ... }, "seq": 42, "stateVersion": { ... } }
```

`seq` and `stateVersion` are optional on event frames; the gateway emits them on events that carry ordered state (chat deltas, session.message, presence). Side-effecting RPC methods require an `idempotencyKey` field in their params to enable safe retries.

### 1.3 Handshake — connect.challenge to hello-ok

The connection establishment is a **three-step challenge-response**:

**Step 1 — Gateway sends challenge (immediately on WS open):**

```json
{
  "type": "event",
  "event": "connect.challenge",
  "payload": { "nonce": "<random-string>", "ts": 1737264000000 }
}
```

The client must respond within the challenge timeout window (default 10 000 ms, clamped 250–10 000 ms; override via `OPENCLAW_CONNECT_CHALLENGE_TIMEOUT_MS` env var).

**Step 2 — Client sends connect request:**

```json
{
  "type": "req",
  "id": "<uuid>",
  "method": "connect",
  "params": {
    "minProtocol": 3,
    "maxProtocol": 3,
    "client": {
      "id": "<client-uuid>",
      "version": "<client-version>",
      "platform": "linux",
      "mode": "operator"
    },
    "role": "operator",
    "scopes": ["operator.read", "operator.write"],
    "device": {
      "id": "<device-fingerprint>",
      "publicKey": "<base64-ed25519-public-key>",
      "nonce": "<same-nonce-from-challenge>",
      "signature": "<v3-signature-payload>"
    },
    "auth": {
      "token": "<shared-secret-token>"
    },
    "locale": "en-US",
    "userAgent": "taos-chat-router/1.0"
  }
}
```

Key fields:
- `minProtocol` / `maxProtocol`: version negotiation; server rejects mismatches
- `role`: `"operator"` (control plane) or `"node"` (capability host)
- `scopes`: subset of operator scopes being requested (see §1.4)
- `device.id`: stable fingerprint derived from the device's keypair; must match the public key
- `device.publicKey`: ed25519 public key in base64
- `device.nonce`: the nonce value from the server's challenge (not a new nonce)
- `device.signature`: v3 signature payload binding platform, deviceFamily, device/client/role/scopes/token/nonce fields together
- `auth.token`: shared-secret token (see §1.5 for all auth modes)
- `auth.password`: optional, orthogonal to token, always forwarded when present
- `auth.bootstrapToken`: sent only when neither token nor deviceToken is available

**Step 3 — Gateway responds with hello-ok:**

```json
{
  "type": "res",
  "id": "<same-id-as-req>",
  "ok": true,
  "payload": {
    "type": "hello-ok",
    "protocol": 3,
    "server": { "version": "2026.4.16", "connId": "<connection-uuid>" },
    "features": {
      "methods": ["chat.send", "sessions.send", "sessions.messages.subscribe", "..."],
      "events": ["chat", "session.message", "presence", "tick", "health", "..."]
    },
    "snapshot": {
      "presence": [...],
      "health": { ... },
      "stateVersion": { ... },
      "uptimeMs": 12345678,
      "configPath": "/root/.openclaw/openclaw.json",
      "stateDir": "/root/.openclaw",
      "sessionDefaults": { ... },
      "authMode": "token"
    },
    "auth": {
      "deviceToken": "<issued-token>",
      "role": "operator",
      "scopes": ["operator.read", "operator.write"]
    },
    "policy": {
      "maxPayload": 26214400,
      "maxBufferedBytes": 52428800,
      "tickIntervalMs": 15000
    }
  }
}
```

Clients must honour `policy.tickIntervalMs` over pre-handshake defaults. The gateway closes the connection with code `4000` if it receives no tick within `tickIntervalMs * 2` ms of silence.

**Client-side connect auth assembly** (`selectConnectAuth` in `src/gateway/client.ts`):

Priority order for `auth.token`:
1. Explicit shared token configured in client
2. Explicit `deviceToken` parameter
3. Stored per-device token (keyed by `deviceId` + `role`)

`auth.bootstrapToken` is sent only when none of the above resolves a token. Auto-promotion of a stored device token on `AUTH_TOKEN_MISMATCH` retry is gated to trusted endpoints only (loopback or `wss://` with pinned `tlsFingerprint`).

**After a successful handshake**, the gateway issues a device token in `hello-ok.auth.deviceToken`. Clients should persist this token; reconnecting with it reuses the approved scope set without re-authenticating via shared secret.

**Device token rotation / revocation** requires `operator.pairing` scope. Token issuance stays bounded to the role set recorded in the device's pairing entry.

### 1.4 Roles and scopes

**Roles:**
- `operator` — control plane client (CLI, web UI, taOS chat router, automation)
- `node` — capability host (camera, screen, canvas, `system.run` execution)

**Operator scopes** (sorted by privilege):
| Scope | Required for |
|---|---|
| `operator.read` | All read operations, status, session list, model list |
| `operator.write` | Send messages, create sessions, patch sessions |
| `operator.admin` | `config.*`, `exec.approvals.*`, `wizard.*`, `update.*` |
| `operator.approvals` | `exec.approval.resolve`, `exec.approval.waitDecision` |
| `operator.pairing` | `device.token.rotate`, `device.token.revoke` |
| `operator.talk.secrets` | `talk.config` with `includeSecrets` |

Plugin-registered gateway RPC methods may request their own custom scope, but the reserved core admin prefixes listed above always resolve to `operator.admin`.

**Node capabilities** (`caps`, `commands`, `permissions`) are declared at connect time and treated as claims; the gateway enforces server-side allowlists.

### 1.5 Authentication modes

There are four authentication modes, set via `gateway.auth.mode` in `openclaw.json`.

#### Mode: `token` (default, recommended for taOS)

Clients must present `auth.token` in the connect payload. The gateway validates it via constant-time comparison. This is the only mode taOS should use for non-loopback deployments.

**What taOS must configure:**
- Set `gateway.auth.mode: "token"` and `gateway.auth.token: "<secret>"` in `openclaw.json`
- Inject the same `<secret>` into the taOS chat router as `OPENCLAW_GATEWAY_TOKEN`
- On first deploy, generate a strong random token via `openssl rand -hex 32` or equivalent; store in taOS secrets DB

**Credential minting / rotation:**
- Token lives in `openclaw.json` (or via SecretRef pointing to an env var)
- Rotation: write new token to config, rotate in taOS secrets DB, restart openclaw service; chat router reconnects with new token from secrets DB automatically

#### Mode: `password`

Same as token but uses `auth.password` instead. Documentation recommends using `OPENCLAW_GATEWAY_PASSWORD` env var. Not preferred for taOS — use token mode.

#### Mode: `trusted-proxy`

Delegates identity verification to a reverse proxy that injects user identity via HTTP headers. Requires `gateway.auth.mode: "trusted-proxy"` and proper trusted-proxy IP configuration. Not suitable for taOS's deployment model (no reverse proxy between taOS and openclaw inside a container).

#### Mode: `none` (`private-ingress`)

Skips shared-secret connect auth entirely. Only appropriate for fully isolated deployments where network access is the only guard. Acceptable inside a locked-down LXC container if the container's network interface is not externally reachable — but token mode is still preferred.

**What taOS must configure for `none`:** Set `gateway.auth.mode: "none"` in `openclaw.json`. No credentials needed. Risk: any process inside the container that opens a WS connection gets full operator access.

#### Mode: `tailscale-serve` (`allowTailscale: true`)

Identity-bearing mode satisfying connect auth from Tailscale request headers. Not relevant for taOS's containerised deployment.

**Auth failure recovery hints** in `error.details`:
- `canRetryWithDeviceToken` (boolean)
- `recommendedNextStep`: `retry_with_device_token` | `update_auth_configuration` | `update_auth_credentials` | `wait_then_retry` | `review_auth_configuration`

### 1.6 RPC method catalog

Methods are sent as `{ "type": "req", "id": "...", "method": "<name>", "params": { ... } }`. The gateway correlates responses by `id`. Non-streaming methods return a single `res` frame. Streaming methods (those that use subscription events) return an immediate `res` and then emit `event` frames until unsubscribed or the run completes.

The full method surface is large; the complete list is in §1.6.0. The methods critical to taOS integration are documented field-by-field below.

#### 1.6.0 Full method inventory (by family)

**System and identity:** `health`, `status`, `gateway.identity.get`, `system-presence`, `system-event`, `last-heartbeat`, `set-heartbeats`

**Models and usage:** `models.list`, `usage.status`, `usage.cost`, `doctor.memory.status`, `sessions.usage`, `sessions.usage.timeseries`, `sessions.usage.logs`

**Channels and login:** `channels.status`, `channels.logout`, `web.login.start`, `web.login.wait`, `push.test`, `voicewake.get`, `voicewake.set`

**Messaging and logs:** `send`, `logs.tail`

**Talk and TTS:** `talk.config`, `talk.mode`, `talk.speak`, `tts.status`, `tts.providers`, `tts.enable`, `tts.disable`, `tts.setProvider`, `tts.convert`

**Secrets, config, update, wizard:** `secrets.reload`, `secrets.resolve`, `config.get`, `config.set`, `config.patch`, `config.apply`, `config.schema`, `config.schema.lookup`, `update.run`, `wizard.start`, `wizard.next`, `wizard.status`, `wizard.cancel`

**Agents and workspace:** `agents.list`, `agents.create`, `agents.update`, `agents.delete`, `agents.files.list`, `agents.files.get`, `agents.files.set`, `agent.identity.get`, `agent.wait`

**Session control:** `sessions.list`, `sessions.subscribe`, `sessions.unsubscribe`, `sessions.messages.subscribe`, `sessions.messages.unsubscribe`, `sessions.preview`, `sessions.resolve`, `sessions.create`, `sessions.send`, `sessions.steer`, `sessions.abort`, `sessions.patch`, `sessions.reset`, `sessions.delete`, `sessions.compact`, `sessions.get`

**Legacy chat:** `chat.history`, `chat.send`, `chat.abort`, `chat.inject`

**Device pairing and tokens:** `device.pair.list`, `device.pair.approve`, `device.pair.reject`, `device.pair.remove`, `device.token.rotate`, `device.token.revoke`

**Node pairing and invoke:** `node.pair.request`, `node.pair.list`, `node.pair.approve`, `node.pair.reject`, `node.pair.verify`, `node.list`, `node.describe`, `node.rename`, `node.invoke`, `node.invoke.result`, `node.event`, `node.canvas.capability.refresh`, `node.pending.pull`, `node.pending.ack`, `node.pending.enqueue`, `node.pending.drain`

**Exec approvals:** `exec.approval.request`, `exec.approval.get`, `exec.approval.list`, `exec.approval.resolve`, `exec.approval.waitDecision`, `exec.approvals.get`, `exec.approvals.set`, `exec.approvals.node.get`, `exec.approvals.node.set`

**Plugin approvals:** `plugin.approval.request`, `plugin.approval.list`, `plugin.approval.waitDecision`, `plugin.approval.resolve`

**Automation:** `wake`, `cron.list`, `cron.status`, `cron.add`, `cron.update`, `cron.remove`, `cron.run`, `cron.runs`

**Skills / tools:** `commands.list`, `skills.status`, `skills.search`, `skills.detail`, `skills.install`, `skills.update`, `tools.catalog`, `tools.effective`

#### 1.6.1 `chat.send`

**Scope required:** `operator.write`

**Params** (`ChatSendParamsSchema`):
| Field | Type | Required | Notes |
|---|---|---|---|
| `sessionKey` | string | yes | Target session identifier |
| `message` | string | yes | The user message text |
| `thinking` | string | no | Scratch-pad context (hidden from end user) |
| `deliver` | boolean | no | Request outbound channel delivery in addition to session execution |
| `originatingChannel` | string | no | Source channel identifier (for audit trail) |
| `originatingTo` | string | no | Source channel recipient |
| `originatingAccountId` | string | no | Source channel account |
| `originatingThreadId` | string | no | Source channel thread |
| `attachments` | array | no | Attachment objects (type is `Unknown` in schema — see §3) |
| `timeoutMs` | integer | no | Per-request timeout override |
| `systemInputProvenance` | object | no | Tracing / provenance metadata |
| `systemProvenanceReceipt` | string | no | Opaque receipt from prior provenance |
| `idempotencyKey` | string | yes | Required for safe retry; use UUID per send |

**Behaviour:**
- Returns immediately with a `res` frame acknowledging the send (does not block for agent completion)
- The agent run proceeds asynchronously; streaming deltas arrive as `chat` events on the WS connection
- If `deliver: true`, openclaw also attempts outbound delivery to the originating channel after the run completes
- With `bestEffortDeliver: false` (default): unresolved delivery targets return `INVALID_REQUEST`
- With `bestEffortDeliver: true`: falls back to session-only execution when external routes are unavailable

**taOS usage:** taOS chat router calls `chat.send` when a user sends a message to an openclaw agent's DM channel. The `sessionKey` is the agent's persistent session key. `idempotencyKey` is a UUID minted per send. `deliver` is `false` (taOS owns delivery, not openclaw channels).

#### 1.6.2 `sessions.send`

Functionally equivalent to `chat.send` but using the newer sessions API family. Params include `key` (session key), `message`, optional `thinking`, `attachments`, `timeoutMs`, and `idempotencyKey`. Prefer `sessions.send` for new implementations; `chat.send` is the legacy WebChat path maintained for UI compatibility.

#### 1.6.3 `chat.history`

**Scope required:** `operator.read`

**Params** (`ChatHistoryParamsSchema`):
| Field | Type | Required | Notes |
|---|---|---|---|
| `sessionKey` | string | yes | Target session |
| `limit` | integer (1–1000) | no | Max messages to return |
| `maxChars` | integer (1–500 000) | no | Total chars cap across returned messages |

**Behaviour:** Returns display-normalised transcript — directives, tool-call internals, and raw tokens are stripped. This is the UI-facing transcript, not the raw model input. For raw transcripts use `sessions.preview` or `sessions.get`.

#### 1.6.4 `chat.abort` / `sessions.abort`

**Scope required:** `operator.write`

**`chat.abort` params:**
| Field | Type | Required | Notes |
|---|---|---|---|
| `sessionKey` | string | yes | Session to abort |
| `runId` | string | no | Target specific run; omit to abort whatever is active |

**Behaviour:** Interrupts the active agent run for the given session. The gateway emits a `chat` event with `state: "aborted"` on the subscriber stream. Safe to call when no run is active (no-op).

**taOS usage:** Wire to the taOS UI "stop generation" button. The chat router sends `chat.abort` for the session associated with the active agent DM.

#### 1.6.5 `sessions.subscribe` / `sessions.unsubscribe`

Subscribe to session-level changes (session metadata, status, compaction). Returns `res` immediately; thereafter emits `sessions.changed` events.

Params: `{}` (subscribes to all sessions). Call once per connection after handshake.

#### 1.6.6 `sessions.messages.subscribe` / `sessions.messages.unsubscribe`

**This is the primary streaming surface for taOS.**

**Scope required:** `operator.read`

**Subscribe params:**
```json
{ "key": "<sessionKey>" }
```

Single required field. Call once per agent session after handshake. The gateway then emits `session.message` events and `chat` events for all activity on that session — including streaming deltas from the active run.

**Unsubscribe params:** same shape `{ "key": "<sessionKey>" }`.

**taOS must subscribe for each openclaw agent session on startup and re-subscribe after reconnect.**

#### 1.6.7 `node.invoke`

**Scope required:** `operator.write`

**Params** (`NodeInvokeParamsSchema`):
| Field | Type | Required | Notes |
|---|---|---|---|
| `nodeId` | string | yes | Target node identifier |
| `command` | string | yes | Command name to invoke on the node |
| `params` | unknown | no | Command-specific parameters |
| `timeoutMs` | integer (≥0) | no | Timeout for this invocation |
| `idempotencyKey` | string | yes | Required for idempotent delivery |

**Behaviour:** Forwards the command to the named connected node. Result arrives asynchronously as a `node.invoke.result` response or via `node.pending.pull` for offline nodes.

#### 1.6.8 `node.list`

Params: `{}`. Returns list of known and connected nodes.

#### 1.6.9 `node.pending.pull`

Used by connected nodes to drain their pending-work queue. Params: `{ maxItems: 1-10 }` (optional). Returns queued `NodePendingDrainItem` objects with `id`, `type`, `priority`, `createdAtMs`, `expiresAtMs`, and `payload`.

Pending item types: `"status.request"` | `"location.request"`.

#### 1.6.10 `config.get`

**Scope required:** `operator.admin`

Params: `{}`. Returns current config snapshot and a hash (for optimistic concurrency on `config.set`).

#### 1.6.11 `config.set` / `config.patch` / `config.apply`

**Scope required:** `operator.admin`

| Method | Behaviour |
|---|---|
| `config.set` | Write validated config payload (`raw`: required non-empty string, `baseHash`: optional for optimistic lock) |
| `config.patch` | Merge partial config update (same fields plus `sessionKey`, `deliveryContext`, `note`, `restartDelayMs`) |
| `config.apply` | Validate and replace full config |

Hot reload applies most changes immediately. Changes to `gateway.*` (port, TLS, auth) require a process restart.

#### 1.6.12 `config.schema`

Params: `{}`. Returns live config schema with UI hints and metadata: `{ schema, uiHints, version, generatedAt }`.

`config.schema.lookup` accepts a path string (alphanumeric, `_`, `/`, `[]`, `-`, `*`) and returns path-scoped schema plus child summaries.

#### 1.6.13 `exec.approval.resolve`

**Scope required:** `operator.approvals`

**Params:**
| Field | Type | Required |
|---|---|---|
| `id` | string | yes |
| `decision` | string (non-empty) | yes |

Resolves a pending exec approval. The `decision` values are not constrained by the schema — in practice `"allow"` and `"deny"` are used. After `"allow"`, the gateway re-runs the approved command using the canonical `systemRunPlan` from the original approval request.

**Security note:** After approval, mutating `command`, `rawCommand`, `cwd`, `agentId`, or `sessionKey` between prepare and final approval causes the gateway to reject the run.

#### 1.6.14 `exec.approval.waitDecision`

**Scope required:** `operator.approvals`

Long-polls until a decision is made on a pending approval request. The approver calls this to block until a taOS UI response resolves it. The gateway broadcasts `exec.approval.requested` when a new approval is needed, and `exec.approval.resolved` when resolved.

**`exec.approval.request` params** (`ExecApprovalRequestParamsSchema`):
- `command`, `rawCommand`, `cwd`: command details
- `systemRunPlan`: canonical argv/cwd/commandText plus session metadata
- `env`: environment variables (secrets are redacted — see changelog 2026.4.15)
- `nodeId` / `host` / `agentId` / `sessionKey`: context
- `security` / `ask` overrides
- `turnSourceChannel`, `turnSourceTo`, `turnSourceAccountId`, `turnSourceThreadId`: delivery context

#### 1.6.15 `system-presence`

Params: `{}`. Returns current presence snapshot for all connected devices. Each presence entry includes `deviceId`, `roles`, and `scopes`. The gateway broadcasts `presence` events on any connection or disconnection.

### 1.7 Event types

Events are emitted to subscribing connections without a prior request. The `event` field is the discriminator.

#### `chat`

The primary streaming event for active runs. Emitted by the gateway as the LLM produces tokens.

**Payload** (`ChatEventSchema`):
| Field | Type | Always present | Notes |
|---|---|---|---|
| `runId` | string | yes | Unique ID for this agent run |
| `sessionKey` | string | yes | Session this run belongs to |
| `seq` | integer (≥0) | yes | Monotonically increasing sequence number within the run |
| `state` | `"delta"` \| `"final"` \| `"aborted"` \| `"error"` | yes | Current state of the stream |
| `message` | unknown | no | Token delta or final message content (type is `Unknown`; in practice a string or structured message object) |
| `usage` | unknown | no | Token usage data; present on `"final"` |
| `stopReason` | string | no | Why generation stopped; present on `"final"` |
| `errorMessage` | string | no | Human-readable error; present on `"error"` |
| `errorKind` | `"refusal"` \| `"timeout"` \| `"rate_limit"` \| `"context_length"` \| `"unknown"` | no | Machine-readable error category |

**Streaming contract:**
- A run emits zero or more `state: "delta"` events followed by exactly one terminal event (`"final"`, `"aborted"`, or `"error"`)
- `seq` starts at 0 and increments by 1 per frame; gaps indicate missed frames (taOS should detect and re-subscribe)
- `message` on `delta` frames is a token or partial content; on `final` frames it is the complete assembled message
- A taOS chat relay should accumulate delta `message` values and emit a full `edit_message` on `final`

**Note:** The `message` field type is `Type.Optional(Type.Unknown())` in the schema — the exact runtime shape is not schema-enforced. Primary observation from the chat event: it is a string for text-only agents; for tool-calling agents it may be a structured object.

#### `session.message`

Emitted when a session transcript is updated. This event accompanies `chat` events and carries the full session transcript delta (not just token text). Subscribers receive these after calling `sessions.messages.subscribe`.

**Note on `session.message` vs `chat`:** The `chat` event carries streaming token deltas as the model runs. The `session.message` event carries the transcript-level update (tool calls, tool results, assistant messages appended to history). taOS should subscribe to `sessions.messages.subscribe` and relay `chat` events for live streaming, then use `session.message` events to keep the persistent trace store updated.

#### `sessions.changed`

Emitted when the session index or session metadata changes. Subscribers active via `sessions.subscribe` receive these.

#### `presence`

Emitted on any device connection or disconnection. Payload is a full presence snapshot. Useful for taOS to detect when the openclaw gateway restarts or loses connected nodes.

#### `tick`

Periodic keepalive. Payload: `{ "ts": <unix-ms> }`. Emitted every `tickIntervalMs` ms (typically 15 000 ms as advertised in `hello-ok.policy`, default 30 000 ms before handshake). The taOS WS client must treat tick silence exceeding `tickIntervalMs * 2` as a dead connection and reconnect.

#### `health`

Gateway health snapshot update. Useful for surfacing openclaw status in taOS agent health dashboards.

#### `heartbeat`

Heartbeat event stream. Separate from tick; carries gateway heartbeat state for long-running operation monitoring.

#### `exec.approval.requested`

Broadcast when an agent run needs user approval to execute a shell command. Payload includes approval `id`, `command`, `rawCommand`, `cwd`, `agentId`, `sessionKey`, and `severity`. taOS UI should surface this as an approval card in the chat thread.

#### `exec.approval.resolved`

Broadcast after an approval is resolved (allow or deny). Payload: `{ id, decision }`.

#### `node.pair.requested` / `node.pair.resolved`

Pairing lifecycle events for node connections.

#### `device.pair.requested` / `device.pair.resolved`

Pairing lifecycle events for operator device connections. `device.pair.requested` payload: `requestId`, `deviceId`, `publicKey`, `platform`, `deviceFamily`, `clientId`, `role`, `scopes`, `remoteIp`, `ts`.

#### `cron`

Emitted on cron job change or run completion.

#### `shutdown`

Gateway shutdown notification. Payload: `{ reason, restartIn? }`. taOS chat router should stop reconnect attempts temporarily when `restartIn` is present and retry after that interval.

### 1.8 Streaming semantics

OpenClaw emits **token-by-token deltas**. Each `chat` event with `state: "delta"` carries a fragment of the assistant's response in `message`. The fragments accumulate into the final response confirmed by the `state: "final"` event.

`seq` is a monotonically-increasing counter within a single `runId`. taOS chat relay logic:

1. On first `delta` for a `runId`, create a new `message_delta` broadcast and a placeholder message in the chat store.
2. On each subsequent `delta`, append the `message` fragment and re-broadcast.
3. On `final`, write the complete `message` content to the chat store via `edit_message`, record `usage` in the trace store, and close the run.
4. On `aborted` or `error`, write an appropriate status message.
5. If a gap in `seq` is detected, log a warning. The protocol does not define a resync mechanism; in practice, calling `chat.history` or `sessions.preview` recovers the last complete transcript.

The `stateVersion` field on `event` frames carries gateway state vector information used for optimistic-concurrency checks by the Control UI. taOS does not need to interpret it, but should pass it through if forwarding state.

### 1.9 Policy limits

From `hello-ok.policy` and `src/gateway/server-constants.ts`:

| Constant | Value | Notes |
|---|---|---|
| `maxPayload` | 26 214 400 bytes (25 MB) | Per-frame WS payload limit |
| `maxBufferedBytes` | 52 428 800 bytes (50 MB) | Per-connection send buffer |
| Pre-auth payload cap | 65 536 bytes (64 KB) | Before handshake completes |
| Default chat history limit | 6 291 456 bytes (6 MB) | Keeps responses under client WS limits |
| `tickIntervalMs` | 15 000 ms (advertised); 30 000 ms (pre-handshake default) | Keepalive interval |
| Control-plane write RPCs | 3 req / 60 s | Rate limit on write operations |
| Request timeout (per RPC) | 30 000 ms | Default; streaming requests have no timeout |
| Initial reconnect backoff | 1 000 ms | Doubles to 30 000 ms ceiling |

The deduplication TTL is 5 minutes with a cap of 1000 entries per connection, relevant for idempotency-key enforcement.

### 1.10 Client constants summary

| Constant | Value | Source |
|---|---|---|
| `PROTOCOL_VERSION` | `3` | `src/gateway/protocol/schema/protocol-schemas.ts` |
| Request timeout | `30 000` ms | `src/gateway/client.ts` |
| Preauth / challenge timeout | `10 000` ms (clamp 250–10 000) | `src/gateway/handshake-timeouts.ts` |
| Initial reconnect backoff | `1 000` ms | `src/gateway/client.ts` |
| Max reconnect backoff | `30 000` ms | `src/gateway/client.ts` |
| Fast-retry clamp (device-token close) | `250` ms | `src/gateway/client.ts` |
| Force-stop grace | `250` ms | `FORCE_STOP_TERMINATE_GRACE_MS` |
| `stopAndWait()` default | `1 000` ms | `STOP_AND_WAIT_TIMEOUT_MS` |
| Default tick (pre-handshake) | `30 000` ms | `src/gateway/client.ts` |
| Tick-timeout close | code `4000` when silence > `tickIntervalMs * 2` | `src/gateway/client.ts` |

---

## 2. Installation and runtime

**Primary sources:** `README.md`, `package.json`, install docs at docs.openclaw.ai, `openclaw gateway --port 18789 --verbose` from README quick start.

### 2.1 Supported platforms

| Platform | Architecture | Status |
|---|---|---|
| Linux (Debian, Ubuntu, Fedora, Arch, NixOS) | x86_64, arm64 | Supported |
| macOS | arm64 (Apple Silicon), x86_64 | Supported |
| Windows native | x86_64 | Supported (WSL2 recommended for stability) |
| WSL2 | x86_64 | Supported, more stable than native Windows |
| Docker / Podman | x86_64, arm64 | Official images available |
| Kubernetes | x86_64, arm64 | Supported via Helm / manifests |
| Orange Pi 5 Plus (arm64) | arm64 | Works — same as Linux arm64 |

OpenClaw is distributed as an npm package. The runtime is Node.js; no native binaries are compiled during install.

### 2.2 Node version requirement

```
"engines": { "node": ">=22.14.0" }
```

Version 24 is recommended. The README quick start references `Node 22.16+` as minimum but `package.json` says `>=22.14.0`. taOS should pin Node 24 in the container base image for maximum compatibility.

The package manager is `pnpm@10.32.1` — only needed for source builds. Global npm installs work with npm, pnpm, or bun.

> **taOS deployer note:** Debian bookworm's default `nodejs` apt package ships version 18, which does not satisfy openclaw's engine requirement. The deployer must install Node 22.19+ (or 24) via NodeSource before running `npm install -g openclaw`. The install script (`app-catalog/agents/openclaw/scripts/install.sh`) enforces `>=22.19` (not just `>=22.14.0`) because earlier 22.x releases have known issues with openclaw's dependency tree; it uses NodeSource `setup_22.x` and validates the minor version explicitly.

### 2.3 Install methods

**Global npm install (taOS's path, as-built):**
```bash
npm install -g openclaw@latest
# taOS does NOT call `openclaw onboard --install-daemon` — see §2.7.
# install.sh writes openclaw.json + env directly and installs its own
# system-level openclaw.service unit.
```

**`openclaw onboard --install-daemon`** does the following (for reference; taOS bypasses this):
- Creates `~/.openclaw/` directory and initial `openclaw.json` config
- On Linux/WSL2: installs a **systemd user service** (`openclaw.service` under `~/.config/systemd/user/`)
- On macOS: installs a **LaunchAgent** (`~/Library/LaunchAgents/ai.openclaw.gateway.plist`)
- On Windows: installs a Scheduled Task with Startup-folder fallback
- Starts the daemon immediately

For a **system-wide service** inside a container running as root, taOS should bypass `onboard --install-daemon` and write its own systemd unit (see §7.1).

**Other install methods** (not relevant to taOS containers):
- Local prefix installer (`install-cli.sh`): keeps openclaw under `~/.openclaw`, no system Node
- Source checkout with pnpm build workflow
- Docker / Podman images

### 2.4 Port binding

- Default gateway port: **18789** (TCP)
- Configurable via `gateway.port` in `openclaw.json` or `--port` CLI flag
- Default bind host: `127.0.0.1` (loopback only)
- External binding: set `gateway.bind: "lan"` in `openclaw.json` (binds `0.0.0.0`); see §5.4 for full bind mode options
<!-- source: github.com/openclaw/openclaw/blob/main/docs/gateway/configuration-reference.md -->
- TLS: configured via `gateway.tls` (cert path, key path); clients pin fingerprint via `gateway.remote.tlsFingerprint`

For taOS: inside an LXC container, the chat router runs on the host and connects to the container's IP on port 18789. The bind host must allow the container-to-host network interface; `0.0.0.0` is simplest and correct since the LXC network namespace provides isolation.

### 2.5 Data directories

From `README.md` and snapshot schema (`stateDir` field in hello-ok):

| Path | Purpose |
|---|---|
| `~/.openclaw/openclaw.json` | Main config file |
| `~/.openclaw/workspace` (or `agents.defaults.workspace` override) | Default agent workspace root |
| `~/.openclaw/agents/<agentId>/agent/models.json` | Per-agent model overrides |
| `~/.openclaw/` | State directory (reported as `stateDir` in hello-ok snapshot) |

For taOS's framework-agnostic-runtime rule: `/root` is bind-mounted from `{data_dir}/agent-home/{slug}/` on the host. This means `~/.openclaw/` — which resolves to `/root/.openclaw/` inside the container — lives on the host and survives container destroy/recreate. No state is lost on container rebuild.

The stub install script already creates `/root/.openclaw/env` correctly; the real integration inherits this.

### 2.6 Log locations

**Primary sources for log paths:** no dedicated docs page found (404). Inferred from the stub install script and systemd/journald conventions:

- **journald**: `journalctl -u openclaw.service` (systemd unit logs to journal by default)
- **File log**: `logs.tail` RPC returns lines from the configured gateway file log with cursor/limit controls — log path returned in the `LogsTailResultSchema.file` field; not hardcoded
- The `openclaw doctor` CLI command checks log status and reports issues

For taOS: log ingestion via `logs.tail` RPC is the right approach, not parsing log files directly.

### 2.7 `openclaw onboard --install-daemon` details

The onboard wizard:
1. Validates system requirements (Node version, etc.)
2. Writes `~/.openclaw/openclaw.json` with prompted config (model provider API key, etc.)
3. Installs platform daemon (systemd user service on Linux)
4. Starts the gateway

**For taOS containers:** taOS should NOT call `openclaw onboard --install-daemon` because:
- It prompts interactively for config
- It installs a user service, not a system service
- taOS deploys a pre-generated `openclaw.json`

Instead, taOS's `install.sh` should:
1. `npm install -g openclaw@latest`
2. Write `/root/.openclaw/openclaw.json` from the taOS deployer template
3. Install a custom system-level `openclaw.service` unit (see §7.1)

### 2.8 Bundle size and disk cost

The manifest currently specifies `disk_mb: 500`. Based on openclaw's dependency tree (includes Anthropic SDK, AWS Bedrock SDK, Google GenAI, OpenAI SDK, multiple messaging SDKs — grammY, Bolt, Baileys, matrix-js-sdk), the real disk footprint with `node_modules` is likely **1–2 GB**. The `500` value in `app-catalog/agents/openclaw/manifest.yaml` is stale. Step 1a in §7 bumps `disk_mb` to `2000`. taOS's arm64 containers should provision at least 2 GB disk for openclaw.

---

## 3. Configuration

**Primary sources:** `docs.openclaw.ai/configuration`, `docs.openclaw.ai/gateway/configuration-reference`, `docs.openclaw.ai/concepts/models`, `docs.openclaw.ai/providers/ollama`.

### 3.1 Config file format

`~/.openclaw/openclaw.json` uses **JSON5 format** (comments, trailing commas allowed). The gateway validates strictly against its schema and refuses to start with unknown keys or malformed values (`openclaw doctor --fix` repairs common issues).

Hot reload is enabled by default (`gateway.reload.mode: "hybrid"`): safe changes apply immediately; `gateway.*` changes (port, TLS, auth) require a process restart. taOS should restart the openclaw service after deployer-written config changes.

### 3.2 Top-level fields

| Field | Purpose |
|---|---|
| `agent` | Global agent defaults (model primary, fallbacks) — simple single-agent config |
| `agents` | Full agents configuration including `defaults`, `models`, `skills`, sandbox, workspace |
| `models` | Provider credentials and model registry (`models.providers`) |
| `channels` | Channel integrations (Discord, Telegram, Slack, Signal, WhatsApp, web, etc.) |
| `gateway` | Server settings: port, auth, TLS, bind host, health, reload mode, push |
| `session` | Conversation scope and reset behaviour (`dmScope`, `resetOnResume`) |
| `hooks` | Webhook endpoints for external event integrations |
| `cron` | Scheduled job definitions |
| `env` | Environment variable management |
| `broadcast` | Advanced broadcast infrastructure |
| `discovery` | mDNS / network discovery settings |
| `plugins` | Plugin paths and configuration |

### 3.3 `models.providers` schema

The `models.providers` object maps provider IDs to credential and endpoint configuration:

```json5
{
  models: {
    providers: {
      "<provider-id>": {
        apiKey: "<string-or-secretref>",
        baseUrl: "https://...",
        organizationId: "optional",
        // provider type inferred from ID or set explicitly
      }
    }
  }
}
```

**Key provider fields:**
| Field | Type | Notes |
|---|---|---|
| `apiKey` | string or SecretRef | API credential; see §3.5 for SecretRef format |
| `baseUrl` | string | Custom endpoint override; takes precedence over provider default |
| `organizationId` | string | Optional billing/org association |
| `api` | enum | Protocol mode (e.g., `"ollama"`, `"openai-completions"`); inferred from provider ID when omitted |
| `allowPrivateNetwork` | boolean | New in 2026.4.15 — must be `true` for self-hosted endpoints on private IPs |
| `headers` | object | Custom HTTP headers; values can use `secretref-env:ENV_VAR_NAME` format |
| `injectNumCtxForOpenAICompat` | boolean | LM Studio / Ollama compat: inject `options.num_ctx`; set `false` if upstream rejects it |

**Merge precedence for per-agent overrides** (`~/.openclaw/agents/<id>/agent/models.json`):
- Non-empty `baseUrl` in agent models.json wins
- Non-empty `apiKey` wins when not SecretRef-managed
- SecretRef-managed credentials refresh from source markers, not resolved values
- Missing fields fall back to top-level `models.providers` config

### 3.4 Pointing openclaw at taOS's LiteLLM proxy

taOS runs LiteLLM on `http://127.0.0.1:4000/v1` (accessible from inside the container as `http://<host-ip>:4000/v1`). To route all openclaw LLM calls through LiteLLM:

```json5
// /root/.openclaw/openclaw.json
{
  agents: {
    defaults: {
      model: {
        primary: "taos/default",
      },
    },
  },
  models: {
    providers: {
      taos: {
        baseUrl: "http://127.0.0.1:4000/v1",
        apiKey: { source: "env", id: "OPENAI_API_KEY" },
        api: "openai-completions",
        allowPrivateNetwork: true,
      },
    },
  },
  channels: {
    // all channels disabled — taOS owns delivery
  },
  gateway: {
    auth: {
      mode: "token",
      token: { source: "env", id: "OPENCLAW_GATEWAY_TOKEN" },
    },
    port: 18789,
  },
}
```

**Important:** The `baseUrl` for an OpenAI-compatible proxy should include the `/v1` path. This differs from the Ollama native API (which uses `http://host:11434` without `/v1`). LiteLLM exposes an OpenAI-compatible API, so `/v1` is correct here. Also set `allowPrivateNetwork: true` since LiteLLM is on a private network address.

The `api: "openai-completions"` mode uses the `/v1/chat/completions` endpoint. Tool calling reliability in this mode depends on the upstream model. Since taOS's LiteLLM proxy handles routing to real models, reliability is governed by the backend model, not openclaw's compat layer. The LM Studio provider doc confirms the same `api: "openai-completions"` shape with `baseUrl` ending in `/v1` — the pattern applies directly to any OpenAI-compatible endpoint including LiteLLM.
<!-- source: github.com/openclaw/openclaw/blob/main/docs/providers/lmstudio.md -->

**Model alias for the LiteLLM default model:**

```json5
// Implementation note (2026-04-18): the provider name is openclaw's built-in
// "litellm" (not a custom "taos" provider). The bridge wires
// models.providers.litellm pointing at LiteLLM on 127.0.0.1:4000 with the
// per-agent virtual key in LITELLM_API_KEY. Default model name is the
// taOS-selected primary, e.g. "litellm/kilo-auto/free".
{
  models: {
    providers: {
      litellm: {
        baseUrl: "http://127.0.0.1:4000",
        apiKey: "${LITELLM_API_KEY}",
        api: "openai-completions",
        models: [{ id: "kilo-auto/free", name: "Kilo Auto Free" }],
      },
    },
  },
  agents: {
    defaults: {
      model: { primary: "litellm/kilo-auto/free" },
    },
  },
}
```

### 3.5 API key handling: SecretRef format

SecretRef markers avoid storing resolved secrets in config files. Three forms:

**Environment variable reference:**
```json5
{ "source": "env", "id": "ENV_VAR_NAME" }
```
or inline in header values: `"secretref-env:ENV_VAR_NAME"`

**File reference:**
```json5
{ "source": "file", "path": "/run/secrets/key" }
```

**Exec reference:**
```json5
{ "source": "exec", "command": "vault kv get -field=key secret/myapp" }
```

**Precedence:** SecretRef-managed credentials refresh from source markers on `secrets.reload` RPC rather than persisting resolved values. This means rotating a key in the env var or file is picked up without rewriting `openclaw.json`.

For taOS, the simplest approach is to write the literal token into `openclaw.json` at deploy time (the file is in `/root/.openclaw/` which is on the host-mounted bind mount, so it's not inside the container image). Alternatively, use `source: "env"` and inject `OPENCLAW_GATEWAY_TOKEN` and `OPENAI_API_KEY` via the container environment.

The stub install script already writes `/root/.openclaw/env` capturing all TAOS/OPENAI env vars as a systemd EnvironmentFile. The real integration should do the same and reference those via SecretRef in `openclaw.json`.

### 3.6 Model alias and default model selection

Model selection hierarchy:
1. `agents.defaults.model.primary` — primary conversation model
2. `agents.defaults.model.fallbacks` — fallback models in sequence
3. Provider auth failover before moving to next model

Specialty model overrides:
- `agents.defaults.imageModel.primary` — image-capable model
- `agents.defaults.pdfModel` — PDF tool (falls back to imageModel then default)
- `agents.defaults.imageGenerationModel` — image creation
- `agents.defaults.videoGenerationModel`, `musicGenerationModel` — media generation
- `agents.defaults.models` — allowlist and aliases catalog

**Resolving unprefixed model names:** alias match → unique configured-provider match → deprecated default provider fallback. All model references normalise to lowercase.

**CLI alias management:**
```bash
openclaw models aliases add <alias> <provider/model>
openclaw models aliases remove <alias>
```

### 3.7 Channels configuration

Channels activate automatically when their config section exists. Each channel supports:

```json5
{
  channels: {
    telegram: {
      enabled: true,
      botToken: "...",
      dmPolicy: "pairing",     // pairing | allowlist | open | disabled
      allowFrom: ["+15551234567"],
    },
    discord: {
      enabled: false,           // disable by setting enabled: false
    },
  },
}
```

Channel config fields (common across all channels):
| Field | Notes |
|---|---|
| `enabled` | boolean; set `false` to disable without removing config |
| `botToken` / `appToken` | Auth credentials (SecretRef supported) |
| `dmPolicy` | `pairing` (default, unknown senders get pairing codes), `allowlist`, `open`, `disabled` |
| `allowFrom` | Array of identifiers allowed to DM the bot |
| `baseUrl` | Override API endpoint (for self-hosted instances) |
| `mode` | Channel-specific operation mode |
| `dbPath` | Per-account state path (WhatsApp stores QR state here) |

**Multiple simultaneous channels:** fully supported. All active channels share the same session/agent routing.

**For taOS MVP:** set all channels to `enabled: false` or omit channel config entirely. taOS's own message hub replaces openclaw's channel routing.

### 3.8 `gateway` config section

| Field | Notes |
|---|---|
| `gateway.auth.mode` | `"token"` \| `"password"` \| `"trusted-proxy"` \| `"none"` |
| `gateway.auth.token` | Shared bearer token (or SecretRef) |
| `gateway.port` | Default `18789` |
| `gateway.bind` | `"loopback"` (default) \| `"lan"` (`0.0.0.0`) \| `"tailnet"` \| `"auto"` \| `"custom"`. Use `"lan"` for container deployments where the host must reach the gateway. <!-- source: github.com/openclaw/openclaw/blob/main/docs/gateway/configuration-reference.md --> |
| `gateway.customBindHost` | Explicit bind address when `gateway.bind` is `"custom"` |
| `gateway.reload.mode` | `"hybrid"` (default) \| `"hot"` \| `"restart"` \| `"off"` |
| `gateway.tls` | TLS cert/key paths |
| `gateway.push.apns.relay.baseUrl` | APNs relay for iOS push (not relevant for taOS) |
| `gateway.controlUi.allowInsecureAuth` | Disable device auth for localhost HTTP compat |
| `gateway.controlUi.dangerouslyDisableDeviceAuth` | Severe security downgrade — do not use |

---

## 4. Extension model

**Primary sources:** `docs.openclaw.ai/plugins`, `src/gateway/protocol/schema/plugin-approvals.ts`, protocol doc (methods: `skills.*`, `tools.catalog`, `tools.effective`, `plugin.approval.*`), awesome-openclaw community list.

### 4.1 Plugin system overview

OpenClaw's extension model is based on **plugins**. Plugins run in-process with the gateway and register capabilities (providers, channels, tools, hooks) via a registration API.

**Two plugin formats are recognised:**
1. **Native plugins** — `openclaw.plugin.json` descriptor plus a runtime module; execute in-process
2. **Bundle plugins** — compatible with Codex/Claude/Cursor layouts (`.codex-plugin/`, `.claude-plugin/`, `.cursor-plugin/`); mapped to openclaw capabilities

**Plugin discovery order:**
1. Paths in the `plugins` config section
2. Workspace extensions
3. Global extensions (`~/.openclaw/plugins/`)
4. Bundled plugins (shipped with openclaw)

**Plugin installation:**
```bash
openclaw plugins install <name>
# Sources resolved in order:
# 1. Local path or archive
# 2. clawhub:<pkg> explicit reference
# 3. npm package
```

A dangerous-code scanner runs on install; bypass with `--dangerously-force-unsafe-install`.

**ClawHub** is the community plugin/skill marketplace with 700+ entries. Plugins install from ClawHub, npm, or local paths.

### 4.2 Plugin lifecycle and registration

New plugins export a `register(api)` entry point (legacy: `activate(api)`):

```typescript
// openclaw-plugin-example/index.ts
export function register(api) {
  api.registerTool({ name: "my_tool", ... });
  api.registerProvider({ ... });
  api.registerChannel({ ... });
  api.registerHook("before_tool_call", handler);
}
```

The `api` object supports:
- `registerTool(definition)` — add a tool callable by the agent
- `registerProvider(definition)` — add a model provider
- `registerChannel(definition)` — add a messaging channel integration
- `registerHook(event, handler)` — intercept lifecycle events
- `registerProvider` variants for speech, media, image/video generation, web operations

Hook semantics: `before_tool_call` with `{ block: true }` is terminal (prevents lower-priority handlers); `{ block: false }` is a no-op pass-through.

**Stability:** The plugin registration API is documented and used by the official bundled plugins. It is public but not marked "stable" with a versioning guarantee in the docs reviewed. Third-party plugins exist in ClawHub (700+), indicating broad real-world usage.

### 4.3 Skills

Skills are higher-level task modules that extend what the agent can do. They are distinct from raw tools — a skill may bundle multiple tools plus prompting logic.

Relevant RPC surface:
- `skills.status` — visible skill inventory for an agent; returns eligibility, requirements, config checks
- `skills.search` / `skills.detail` — ClawHub discovery
- `skills.install` — install from ClawHub (`{ source: "clawhub", slug, version? }`) or gateway installer (`{ name, installId }`)
- `skills.update` — update a ClawHub-tracked skill or patch skill config values

Skills are configured under `agents.defaults.skills` or per-agent.

### 4.4 Tool calling contract

Tools registered via `api.registerTool(definition)` are available to the agent's LLM. Tool invocations follow the standard OpenAI function-calling pattern at the LLM API level. From the gateway protocol perspective:

- `tools.catalog` RPC returns all registered tools grouped by source (`core` or `plugin`) with `pluginId` and `optional` fields
- `tools.effective` RPC returns session-scoped effective tool inventory for a specific `sessionKey`
- Tool execution may generate `exec.approval.requested` events if the tool does shell execution and the approval policy is set to `ask`
- Plugin approvals use a parallel flow: `plugin.approval.request`, `plugin.approval.waitDecision`, `plugin.approval.resolve`

**Plugin approval params** (`PluginApprovalRequestParamsSchema`):
| Field | Type | Notes |
|---|---|---|
| `title` | string | Short description of what needs approval |
| `description` | string | Full approval request text |
| `pluginId` | string | optional |
| `toolName` | string | optional |
| `toolCallId` | string | optional |
| `agentId` | string | optional |
| `sessionKey` | string | optional |
| `severity` | `"info"` \| `"warning"` \| `"critical"` | optional |
| `twoPhase` | boolean | optional — enables two-phase approval flow |
| `timeoutMs` | integer | optional |

### 4.5 MCP integration

OpenClaw includes the `@modelcontextprotocol/sdk` package (version 1.29.0 as of 2026.4.16). MCP server connections are supported as a plugin/skill surface. The exact registration API for MCP servers as tools is not fully documented in the reviewed sources, but the `tools.catalog` RPC's `source` field includes `"plugin"` as a valid provenance, and MCP servers are a type of plugin source.

**taOS implication:** The cleanest path for injecting taOS MCP tools into openclaw is either:
1. Write a taos-mcp-bridge plugin that registers taOS's skills endpoint as an MCP server
2. Configure openclaw directly with `TAOS_SKILLS_MCP_URL` via the plugin config

This is a Phase 2 task.

### 4.6 Connectors / channels as plugins

The `registerChannel(definition)` plugin API allows custom messaging channel integrations. The official channels (Discord, Telegram, Slack, Signal, WhatsApp, iMessage, etc.) are themselves implemented as bundled plugins using this API.

**taOS implication:** taOS does NOT need to implement a channel plugin. Instead, taOS disables openclaw's built-in channels and routes all messages through taOS's own message hub. In the bridge-adapter MVP (see §6 and §7), taOS acts as a channel through the SSE bridge endpoints on the taOS host rather than as a raw WS operator client — this decouples taOS from openclaw's v3 gateway protocol version. The operator-client approach (WS + v3 handshake) is kept as a documented fallback; see §6.

### 4.7 Community ecosystem (relevant to taOS)

From awesome-openclaw:

| Project | Relevance |
|---|---|
| Manifest | Real-time cost observability for OpenClaw agents — may be redundant with taOS's existing LiteLLM cost pipeline |
| crabwalk | Real-time companion monitor — monitoring reference implementation |
| AgentPulse | LLM cost tracking |
| aquaman | Credential isolation proxy — may be useful if taOS manages multiple openclaw instances sharing a gateway |
| leashed | Policy engine with kill switch — potential reference for taOS exec approval UI |
| MobileClaw | PWA with live tool calls (React) — reference for streaming UI implementation |
| openclaw-docker | Official Docker images — useful for integration test environment |

---

## 5. Known limitations and feature gaps

### 5.1 Issue #6467 — Agent Event Stream API

**Status: Closed (as of April 2026). NOT merged as a gateway feature.**

The issue requested a standardised lightweight event-streaming API (Unix domain sockets or TCP) for step-by-step agent progress, tool-call events, sub-agent lifecycle, and LLM token events. The proposed event types (`agent.started`, `tool.call`, `llm.tokens`, etc.) were separate from the existing `chat` event stream.

**Current state:** The `chat` event stream on the gateway WS does cover token-level streaming via `state: "delta"` frames. The issue appears to have been closed without a distinct implementation — the existing gateway WS API was deemed sufficient. However, the granular step-level events (`agent.step_started`, `tool.call`, `tool.output`) are NOT available as distinct event types; they are embedded in the tool-call handling visible via `session.message` events.

**taOS implication:** For MVP streaming, `chat` events are sufficient. For detailed observability (tool call inputs/outputs as distinct events), taOS may need to parse `session.message` events or implement a gap-fill via fork. This is the primary observability gap to monitor.

### 5.2 Issue #67737 — otel-observability hooks not firing

`api.on()` typed hooks never fire for WhatsApp channel messages. Open as of April 2026. Not relevant to taOS (channels are disabled in taOS integration), but indicates the observability hook surface has known gaps.

### 5.3 Multi-tenancy

OpenClaw is explicitly documented as a "single trusted operator trust domain" — one gateway serves one operator. It is NOT designed for hostile multi-tenancy.

**taOS implication:** Each openclaw agent in taOS gets its own container with its own openclaw gateway instance. Do not attempt to share one openclaw gateway across multiple taOS agents. This is already the correct pattern given the framework-agnostic-runtime rule (one container per agent).

### 5.4 `gateway.bind` config key

The config key is `gateway.bind`. Accepted values: `"auto"`, `"loopback"` (default), `"lan"` (binds `0.0.0.0`), `"tailnet"` (Tailscale IP only), or `"custom"` (paired with `gateway.customBindHost`). Legacy raw host strings (`0.0.0.0`, `127.0.0.1`, etc.) are not accepted — use the mode values. For taOS container deployments where the gateway must accept connections from the host network, set `gateway.bind: "lan"`.
<!-- source: github.com/openclaw/openclaw/blob/main/docs/gateway/configuration-reference.md -->

**Docker/container note from primary source:** the default `loopback` bind listens on `127.0.0.1` inside the container. With bridge networking (`-p 18789:18789`), incoming traffic arrives on `eth0`, so the gateway is unreachable. Use `bind: "lan"` (or `"custom"` with `customBindHost: "0.0.0.0"`) to listen on all interfaces.

### 5.5 Cost tracking and usage

`usage.status`, `usage.cost`, and `sessions.usage` RPCs provide provider usage windows, quota summaries, and per-session cost data. OpenClaw reports cost per call at the session level.

**taOS implication:** taOS already captures cost via LiteLLM callbacks (the existing trace pipeline). The openclaw cost surface (`usage.cost` RPC) is additive — it can be polled periodically to cross-check, but is not the primary cost store. Mark as "done" in the mapping table.

### 5.6 `session.message` event payload detail

The `session.message` event payload shape is not schema-enforced beyond `Type.Unknown()` in the TypeScript source reviewed. The exact fields (tool call entries, assistant message structure) are not documented in the primary sources reviewed. This is the most significant gap in this document.

**Session lifecycle (from primary source):** sessions are reused until they expire. Default reset: new session at 4:00 AM local time on the gateway host. Optional idle reset: set `session.reset.idleMinutes`. Manual reset: send `/new` or `/reset` in chat. State is owned by the gateway; store path is `~/.openclaw/agents/<agentId>/sessions/sessions.json`; transcripts at `~/.openclaw/agents/<agentId>/sessions/<sessionId>.jsonl`.
<!-- source: github.com/openclaw/openclaw/blob/main/docs/concepts/session.md -->

**Open question — needs primary source:** exact `session.message` event payload shape, especially for tool-call entries.

### 5.7 Rate limits on write RPCs

Control-plane write RPCs are rate-limited to **3 requests per 60 seconds**. For a taOS chat router that handles high-throughput message sends, this is a potential bottleneck. The rate limit applies to control-plane writes, which likely includes `chat.send` — this needs verification.

**Open question — needs primary source:** does the 3 req/60s rate limit apply to `chat.send` / `sessions.send`, or only to config/management RPCs?

### 5.8 `message` field type in `ChatEventSchema`

The `message` field in `ChatEventSchema` is typed as `Type.Optional(Type.Unknown())`. The exact runtime shape (string for text, structured object for tool results) is not schema-enforced. The taOS relay must handle both cases defensively.

### 5.9 Node 22.14 vs 22.16 vs 22.19 discrepancy

`package.json` engine: `>=22.14.0`. README: `Node 22.16+`. taOS's install script enforces
`>=22.19` (full-version check, not just major) because earlier 22.x point releases fail at
runtime even though they satisfy a major-only guard. Pin Node 24 to avoid all ambiguity.

---

## 6. taOS integration mapping (HISTORICAL — superseded)

> **Note:** This mapping table describes the fork-bridge design that was never shipped.
> The live implementation uses upstream npm + ACP (see implementation summary at the top of
> this doc). This section is retained for historical context only.

The planned MVP integration path was the **bridge adapter** — openclaw's fork patch fetching
a bootstrap document from a taOS-hosted endpoint and configuring openclaw's native clients
from it. That design was abandoned in favour of upstream npm + ACP before implementation.

| OpenClaw capability | taOS maps it to | Mechanism | Priority |
|---|---|---|---|
| Bootstrap config load | taOS exposes `GET /api/openclaw/bootstrap` (bearer auth with local token) | openclaw's `src/taos-bridge.ts` patch fetches bootstrap on startup; receives LiteLLM endpoint + API key, qmd URL, MCP URL, channel config, `schema_version` | MVP |
| LLM provider injection | taOS bootstrap supplies `models.providers.litellm` with `baseUrl` + `${LITELLM_API_KEY}` (openclaw's built-in litellm provider, not a custom "taos" provider) | Bridge writes the provider into openclaw's runtime registry; does NOT edit `openclaw.json` on disk | done |
| Outbound channel (user→openclaw) | taOS's chat hub POSTs messages to the openclaw channel adapter via SSE | Bridge patch registers `channels.kind: "external", provider: "taos"`; receives messages via SSE from `{TAOS_BRIDGE_URL}/sessions/{id}/events` | MVP |
| Inbound channel (openclaw→user) | openclaw emits replies + tool events on the external channel | Bridge adapter POSTs to `{TAOS_BRIDGE_URL}/sessions/{id}/reply` with delta/final/error payloads; taOS relays to chat hub | MVP |
| Streaming deltas | openclaw's session message delta → channel → taOS | Bridge adapter hooks session message events and forwards to SSE client; chunk granularity matches openclaw's native stream | MVP |
| Trace capture | taOS writes `llm_call`, `message_in`, `message_out`, `tool_call`, `tool_result`, `error` traces on every event received via bridge | No openclaw-side trace code needed; taOS's bridge endpoint captures as events flow through | MVP |
| `npm install` + systemd unit | Replace Python FastAPI stub in `install.sh` | Install Node 22.14+ via NodeSource; `npm install -g github:jaylfc/openclaw#<sha>`; write `openclaw.service` unit; `After=network-online.target` | MVP |
| `openclaw.json` generation | Deployer writes config at deploy time | Gateway auth `token` mode; empty `channels: {}`; `models.providers.litellm` pointing to LiteLLM at 127.0.0.1:4000 with `${LITELLM_API_KEY}`; `TAOS_BRIDGE_URL` + `TAOS_LOCAL_TOKEN` + `LITELLM_API_KEY` in env | done |
| All channels disabled | Deployer writes `channels: {}` in `openclaw.json` | taOS owns all delivery via chat hub + channel-hub layer; openclaw's built-in channels (Discord, Telegram, etc.) never start | MVP |
| Tool calling (openclaw→MCP) | Bridge bootstrap supplies MCP URL; openclaw uses its existing MCP client to reach taOS's MCP server on the host | Leverages openclaw's existing `registerTool` + MCP SDK; no protocol invention | MVP (if openclaw's MCP client is stable in current fork baseline) |
| Cost tracking | Already wired via LiteLLM callback into taOS trace store | Existing trace pipeline picks up every LLM call by default | done |
| `exec.approval.waitDecision` → bridge adapter → taOS chat hub | taOS UI surfaces approval card in chat thread | Phase 2 — MVP does not ship tool-calling UX | Phase 2 |
| Plugin approval flow | Same as exec approval, plugin-scoped | `plugin.approval.requested` → UI → `plugin.approval.resolve` via bridge | Phase 2 |
| `tools.catalog` / `tools.effective` | Expose tool inventory in taOS agent detail UI | Periodic poll via bridge or `tools.catalog` RPC; display in agent settings | Phase 2 |
| `config.get` / `config.set` | taOS Settings UI shows openclaw config for advanced users | `config.get` on load; `config.set` on save with `baseHash` optimistic lock | Phase 2 |
| `sessions.usage` / `usage.cost` | Cross-check against LiteLLM cost pipeline | Poll `usage.cost` RPC periodically; reconcile with LiteLLM trace store | Phase 2 (existing trace pipeline already covers this) |
| `logs.tail` RPC | Log ingestion for taOS agent log viewer | Poll `logs.tail` with cursor; relay lines to taOS log store | Phase 2 |
| `session.message` event (full transcript delta) | Persist tool-call entries to trace store | Parse session.message events after agent run; write to per-agent trace store | Phase 2 |
| Gateway :18789 | **Not used by MVP**; openclaw's gateway stays internal to the container. Only surfaces if needed for operator ops (logs, config reload, etc.) | Reserved for ops tooling; not a primary integration path | Phase 2 |
| Operator-client v3 WS | **Fallback only** if the bridge adapter proves unworkable. Not recommended MVP path — couples taOS to openclaw's protocol version; reconsidered only on bridge blocker. Was the original design choice prior to the 2026-04-11 bridge-design spec. | Raw WS client with v3 handshake, ed25519 device signing, tick keepalive. See §1 for protocol details. | Fallback |
| `node.invoke` | Not needed | Not applicable — taOS is not a node controller | Deferred |
| `node.pending.pull` | Not needed | Not applicable | Deferred |
| openclaw channels (Discord, Telegram, etc.) | Replaced by taOS message hub | Set `enabled: false`; taOS owns channel routing | Deferred |
| Voice / speech (TTS, talk mode) | Not needed | openclaw TTS is a separate surface; taOS has no voice UI | Deferred |
| Canvas renderer | Not needed | macOS/iOS-specific; taOS is web + desktop | Deferred |
| Cron / automation | Not needed at runtime level | taOS has its own scheduler service | Deferred |
| `wizard.*` RPC family | Not needed | Interactive onboarding; taOS deploys config programmatically | Deferred |
| `update.run` | Not needed | taOS controls updates by rebuilding the container image | Deferred |

---

## 7. MVP-to-full roadmap (HISTORICAL — superseded)

> **Note:** This roadmap was written for the fork-bridge design. It was not executed.
> The live integration is upstream npm + ACP (see implementation summary). Steps below
> are retained for historical record only.

### Step 1: MVP — replace the stub with real openclaw (bridge adapter)

**[NOT IMPLEMENTED — replaced by ACP approach]**

**Original goal:** A real openclaw gateway wired to taOS via the bridge adapter and fork patch.
**Actual implementation:** upstream `npm install -g openclaw@latest` + `openclaw_acp_runtime.py`.

**Review-gate refinements baked into this step:**

- Feature-flag the patch: `if (!process.env.TAOS_BRIDGE_URL) return;` at top of `src/taos-bridge.ts`. With the env unset the forked build behaves identically to upstream — sanity check on every upstream merge.
- Version-stamp the bootstrap: taOS `/api/openclaw/bootstrap` response includes `{"schema_version": 1, ...}`. Patch fails loud if the version is absent.
- Single coupling discipline: the patch only reads `TAOS_BRIDGE_URL` + bootstrap body. No other reaching into openclaw internals. Makes the patch refactor-resistant.
- Channel kind: `channels.kind: "external"` with `provider: "taos"` (not `"taos-bridge"`). A generic `external` kind is plausibly upstreamable; squatting a named kind makes upstream PRs harder.
- Hard cap the patch at 400 LoC. When it trends up, cut scope or find a smaller interface. This is a review gate for the upstream PR.
- Trust anchor: `audited_at` is a freshness marker only. Automated persistence-audit tests from the bridge spec are the real guarantee; no manual `audited_by` field needed.
- Parallel upstream PR with every patch change: public review thread keeps the patch honest and makes eventual upstreaming plausible.

#### 1a. Fork baseline + install script

**Files:** `app-catalog/agents/openclaw/scripts/install.sh`, `app-catalog/agents/openclaw/manifest.yaml`

Fork already exists at `github.com/jaylfc/openclaw`. Pick a current baseline SHA from upstream `main` (verify the fork tracks it; rebase if needed).

New `install.sh` flow:

**[NOT IMPLEMENTED — see implementation summary]**

The install script that shipped (`app-catalog/agents/openclaw/scripts/install.sh`) installs
upstream `openclaw@latest` from npm (not a fork), writes `openclaw.json` and env from
injected env vars, and runs `openclaw gateway` (not `openclaw acp`) as the systemd unit.
The gateway stays running as before; taOS drives turns via ACP, not via the WS gateway.

**Pi/ARM note:** on low-RAM ARM hosts the upstream Pi deployment guide recommends adding a
2 GB swapfile and setting `vm.swappiness=10`, plus `NODE_COMPILE_CACHE=/var/tmp/openclaw-compile-cache`.
<!-- source: github.com/openclaw/openclaw/blob/main/docs/install/raspberry-pi.md -->

#### 1b. Generate `openclaw.json` at deploy time

**File:** `tinyagentos/deployer.py` (or the relevant agent deploy path)

At agent creation / first deploy, the deployer writes `/root/.openclaw/openclaw.json` via the host-mounted bind at `{data_dir}/agent-home/{slug}/.openclaw/openclaw.json`. The file appears at container start via the bind mount; no writes needed inside the container.

Template (minimal MVP config):

```json5
{
  agents: {
    defaults: {
      model: { primary: "taos/default" },
    },
  },
  models: {
    providers: {
      taos: {
        baseUrl: "http://127.0.0.1:4000/v1",   // LiteLLM via incus proxy device
        apiKey: { source: "env", id: "TAOS_LITELLM_VIRTUAL_KEY" },
        api: "openai-completions",
        allowPrivateNetwork: true,
      },
    },
  },
  channels: {},   // taOS owns all delivery; openclaw built-in channels never start
  gateway: {
    auth: {
      mode: "token",
      token: { source: "env", id: "OPENCLAW_GATEWAY_TOKEN" },
    },
    port: 18789,
  },
  session: {
    dmScope: "per-channel-peer",
  },
}
```

Gateway auth token: `secrets.token_urlsafe(32)`, stored in taOS secrets DB.

Bridge env vars injected into the container via incus config / Docker env:
- `TAOS_BRIDGE_URL=http://127.0.0.1:6969/api/openclaw/bootstrap`
- `TAOS_LOCAL_TOKEN=<per-agent bearer token, stored in secrets DB>`

**Test:** `config.get` RPC after handshake returns expected config; `TAOS_BRIDGE_URL` set in container environment.

#### 1c. taOS bootstrap endpoint

**File:** `tinyagentos/routes/openclaw_bridge.py` (new route group)

New route: `GET /api/openclaw/bootstrap` (bearer auth with `TAOS_LOCAL_TOKEN`).

Returns:

```json
{
  "schema_version": 1,
  "agent_name": "research-agent",
  "models": {
    "providers": {
      "taos": {
        "base_url": "http://127.0.0.1:4000/v1",
        "api_key": "<per-agent LiteLLM virtual key>"
      }
    }
  },
  "memory": {
    "qmd_server": "http://127.0.0.1:7832",
    "db_path": "/memory/index.sqlite"
  },
  "skills_mcp_url": "http://127.0.0.1:6970/mcp/<agent-name>",
  "channels": {
    "kind": "external",
    "provider": "taos",
    "events_url": "http://127.0.0.1:6969/api/openclaw/sessions/<agent>/events",
    "reply_url": "http://127.0.0.1:6969/api/openclaw/sessions/<agent>/reply"
  }
}
```

`schema_version` is mandatory. Bridge patch fails loud (process exit or startup error) if `schema_version` is absent or not `1`.

**Test:** Unit test: endpoint returns correct shape; `schema_version` present.

#### 1d. Bridge SSE endpoints on taOS

**File:** `tinyagentos/routes/openclaw_bridge.py`

- `GET /api/openclaw/sessions/{agent}/events` — Server-Sent Events stream carrying inbound user messages + control events. openclaw's bridge channel subscribes on startup; taOS pushes events as users chat via the Messages app.
- `POST /api/openclaw/sessions/{agent}/reply` — openclaw bridge POSTs back deltas/finals/errors/tool-events. taOS ingests these, writes trace records (`llm_call`, `message_in`, `message_out`, `tool_call`, `tool_result`, `error`), and relays to the chat hub.

**Test:** Unit test: SSE endpoint streams events in correct format; reply endpoint ingests and writes trace events.

#### 1e. Fork patch: `src/taos-bridge.ts`

**File:** `src/taos-bridge.ts` in the `jaylfc/openclaw` fork

Single file, feature-flagged entry:

```typescript
export function register(api) {
  if (!process.env.TAOS_BRIDGE_URL) return;  // no-op when env unset — upstream parity

  const bridgeUrl = process.env.TAOS_BRIDGE_URL;
  const localToken = process.env.TAOS_LOCAL_TOKEN;

  // Fetch bootstrap; fail loud on missing schema_version
  const bootstrap = await fetchBootstrap(bridgeUrl, localToken);
  if (bootstrap.schema_version !== 1) {
    throw new Error(`taos-bridge: unsupported schema_version ${bootstrap.schema_version}`);
  }

  // Configure native LLM client from bootstrap (no disk writes)
  api.registerProvider({ kind: "openai", ...bootstrap.models.providers.taos });

  // Register external channel
  api.registerChannel({
    kind: "external",
    provider: "taos",
    eventsUrl: bootstrap.channels.events_url,
    replyUrl: bootstrap.channels.reply_url,
  });
}
```

Hard cap: patch stays under 400 LoC. When it trends up, cut scope or find a smaller interface. Open an upstream PR in parallel with every patch change.

**Test:** Unit test with env unset — `register()` returns without error, no side effects. Unit test with env set — bootstrap fetched, provider registered, channel registered.

#### 1f. Chat router rewrite

**File:** `tinyagentos/routes/chat_router.py` (or equivalent)

Replace the operator-client WS client with bridge relay logic:
- On inbound user message: POST to `GET /api/openclaw/sessions/{agent}/events` SSE stream (push event to openclaw's bridge channel).
- On reply from openclaw (via `POST /api/openclaw/sessions/{agent}/reply`): relay delta/final/error events to the taOS chat hub.

Simpler than the operator-client plan: no WS handshake, no device signing, no ed25519 keypair, no tick keepalive loop.

**Test:** Unit test: mock bridge SSE endpoint; verify chat router pushes user messages and ingests replies.

#### 1g. systemd unit + container boot order

- Replace stub systemd unit with `openclaw gateway` invocation (see 1a for unit content).
- `After=network-online.target`; `Restart=on-failure`.
- `EnvironmentFile=-/root/.openclaw/env` (already exists from earlier stub work).

#### 1h. LiteLLM key rotation caveat

Bridge bootstrap loads at openclaw startup. Per-agent LiteLLM key rotation during archive→restore restarts the container, so bootstrap reloads automatically — safe for that case today.

**Future work (Phase 2):** Add a bootstrap `reload` RPC so hot rotation works without container restart. Until then, key rotation requires restarting the openclaw container.

#### 1i. Testing approach

- **Unit:** Bootstrap endpoint returns correct shape with `schema_version`.
- **Unit:** SSE endpoint streams events in the correct format.
- **Unit:** Reply endpoint ingests and writes `message_in` / `llm_call` / `message_out` trace events with correct `trace_id` linkage.
- **Unit:** Bridge patch with `TAOS_BRIDGE_URL` unset — `register()` is a no-op.
- **Integration (on Pi):** Deploy a fresh openclaw agent, send one message via Messages app, observe reply streams back with deltas, verify trace DB has `message_in` / `llm_call` / `message_out` with correct `trace_id` linkage.
- **Integration (on Pi):** Upstream-merge rebase dry-run — rebase the fork on upstream `main`, rebuild, verify the patch still applies cleanly and the feature flag (`TAOS_BRIDGE_URL` unset) still produces upstream-identical behaviour.

---

### Step 2: Phase 2 — tool calling + approvals

#### 2a. taOS MCP bridge plugin

**Goal:** taOS's skills (registered in the Skills MCP server at `TAOS_SKILLS_MCP_URL`) callable from openclaw agents.

The bridge bootstrap already supplies `skills_mcp_url`. For Phase 2 the bridge patch extends `src/taos-bridge.ts` to call `api.registerTool(...)` for each tool exposed by the MCP server at that URL. This happens inside the bridge adapter, not over a separate WS channel.

**File:** Extend `src/taos-bridge.ts` (stays under 400 LoC ceiling; if it doesn't fit, extract an `src/taos-bridge-mcp.ts` helper called from the main file).

**Risk:** MCP registration API surface in openclaw plugins not fully confirmed from reviewed sources. May need to examine official MCP-enabled plugin examples in the fork baseline before implementing.

**Test:** Call a taOS skill from an openclaw agent via natural language; verify the skill executes and returns a result.

#### 2b. Exec approval relay

**Goal:** openclaw `exec.approval.requested` events surface in taOS UI; user resolves via Messages app.

In the bridge-adapter approach, approval events flow through the bridge's `POST /api/openclaw/sessions/{agent}/reply` endpoint as structured `tool_approval_request` event objects. taOS's bridge endpoint routes them to the chat hub as approval cards.

**Components:**
1. Bridge patch registers a hook for `exec.approval.requested` and POSTs a structured approval event to `/reply`.
2. taOS bridge endpoint recognises `kind: "tool_approval_request"` and emits to chat hub.
3. taOS Messages app renders an approval card component with Allow / Deny buttons.
4. User clicks Allow/Deny → taOS backend POSTs decision to `POST /api/openclaw/sessions/{agent}/reply` as a `tool_approval_response` event; bridge patch resolves the `exec.approval.waitDecision` call.

**Files to create/change:**
- `src/taos-bridge.ts`: add approval hook (inside 400 LoC ceiling or extract helper).
- `tinyagentos/routes/openclaw_bridge.py`: handle `tool_approval_request` and `tool_approval_response` event kinds.
- `desktop/src/apps/messages/`: add `ApprovalCard` component.

**Test:** Trigger an exec approval in openclaw; verify approval card appears in Messages UI; click Allow; verify command executes.

#### 2c. Stop-generation wired to UI stop button

**File:** `desktop/src/apps/messages/` — add stop-generation button.
**File:** `tinyagentos/routes/openclaw_bridge.py` — handle abort request via bridge.

In the bridge-adapter approach, the stop button POSTs a `kind: "abort"` control event to the SSE events stream for the agent's session. The bridge patch in `src/taos-bridge.ts` listens for `abort` control events on the SSE channel and calls openclaw's internal abort mechanism. This avoids the need for a raw WS connection from taOS just for abort.

If the bridge approach proves unworkable for abort (Phase 2 evaluation), fall back to a direct `chat.abort` WS RPC as the escape hatch — the gateway is reserved for this in §6.

**Test:** Start a long-running openclaw generation; click stop; verify generation stops and UI updates.

---

### Step 3: Phase 3 — fork-if-needed

#### 3a. Monitor issue #6467 and observability gap

The existing `chat` event stream covers token streaming but lacks structured step-level events (`tool.call`, `tool.output`, `agent.step_started`). The issue was closed without a distinct implementation. Evaluate quarterly whether:
- The gap is blocking taOS features (e.g., tool call visualisation in Messages UI)
- A contribution to openclaw upstream is feasible
- Forking as `openclaw-taos` is justified

**Contribution-first approach:** OpenClaw is MIT-licensed and Jay prefers proper upstream contributions (per project memory). File a feature request or PR for a structured tool event type in the `session.message` event payload before forking.

#### 3b. If forking

Maintain `openclaw-taos` as a thin fork:
- Branch from `main` at a stable release tag
- Add the structured tool-event fields to `session.message` payload
- Keep diff minimal; track upstream releases
- Document fork rationale in `docs/design/openclaw-fork-rationale.md`
- Schedule monthly upstream sync

---

### Step 4: Deferred

| Feature | Reason deferred |
|---|---|
| openclaw channels (Discord, Telegram, Slack, Signal, WhatsApp) | taOS owns channel routing via its own message hub; openclaw channels disabled at deploy |
| Voice / speech (TTS, talk mode, voice wake) | No voice surface in taOS web UI |
| Canvas renderer | macOS/iOS-specific; taOS is web + desktop |
| `wizard.*` RPCs | Interactive onboarding; replaced by taOS deployer |
| `update.run` RPC | taOS controls updates by rebuilding the container image, not in-process updates |
| Cron (`cron.*` RPCs) | taOS has its own scheduler service |
| `node.invoke` / node management | taOS is an operator client; no node topology managed through openclaw |

---

## Appendix A: Open questions

Items that require fetching additional primary sources before implementation:

1. ~~**Exact bind-host config key**~~ — resolved: `gateway.bind` with values `"loopback"` (default), `"lan"`, `"tailnet"`, `"auto"`, `"custom"`. See §3.8 and §5.4. <!-- source: github.com/openclaw/openclaw/blob/main/docs/gateway/configuration-reference.md -->

2. **v3 device signature payload encoding** — the protocol doc describes the fields but not the exact byte encoding / serialisation format. Read `src/gateway/client.ts` `buildDeviceSignature` or equivalent.

3. **`session.message` event payload shape** — schema types the payload as `Unknown`. Requires reading the gateway's session message emission code or official docs.

4. **Rate limit applicability** — does the 3 req/60s control-plane write limit apply to `chat.send` / `sessions.send`? Check `src/gateway/rateLimit.ts` or equivalent.

5. ~~**`openclaw gateway status` CLI command**~~ — resolved: use `openclaw health` (exits non-zero if gateway unreachable; `--json` for machine-readable output; `--timeout <ms>` for startup wait loops). <!-- source: github.com/openclaw/openclaw/blob/main/docs/cli/health.md -->

6. **MCP server registration in plugins** — exact `api.register*` call for MCP server integration. The plugin API exposes `registerTool`, `registerProvider`, `registerChannel`, `registerHook` (see §4.2), but the MCP-specific registration path is not confirmed from reviewed sources.

7. **Tool call shape in `chat` event `message` field** — runtime shape for tool-calling agents; not schema-enforced.

---

## Appendix B: Primary sources not fetched (404 or gated)

The following URLs returned 404 or were otherwise unavailable at research time. Where the GitHub repo contains a mirror, the recovery path is noted.

- `docs.openclaw.ai/concepts/configuration` — 404; used `docs.openclaw.ai/configuration` instead. No direct repo mirror confirmed; nearest match is `docs/gateway/configuration-reference.md` and `docs/gateway/configuration.md`.
- `docs.openclaw.ai/concepts/plugins` — 404; used `docs.openclaw.ai/plugins` instead. No `docs/concepts/plugins.md` in repo; plugin content is distributed across `docs/gateway/configuration-reference.md` and the plugin registration API documented in §4.
- ~~`docs.openclaw.ai/concepts/sessions`~~ — recovered from `github.com/openclaw/openclaw/blob/main/docs/concepts/session.md`
- ~~`docs.openclaw.ai/gateway/logs`~~ — recovered from `github.com/openclaw/openclaw/blob/main/docs/gateway/logging.md`
- ~~`docs.openclaw.ai/operations/logging`~~ — recovered from `github.com/openclaw/openclaw/blob/main/docs/logging.md`
- ~~`docs.openclaw.ai/remote-access`~~ — recovered from `github.com/openclaw/openclaw/blob/main/docs/gateway/remote.md`
- ~~`docs.openclaw.ai/deploy/docker`~~ — recovered from `github.com/openclaw/openclaw/blob/main/docs/install/docker.md`
- ~~`docs.openclaw.ai/deploy/linux`~~ — recovered from `github.com/openclaw/openclaw/blob/main/docs/vps.md`
- ~~`docs.openclaw.ai/deploy/raspberry-pi`~~ — recovered from `github.com/openclaw/openclaw/blob/main/docs/install/raspberry-pi.md`
- ~~`docs.openclaw.ai/gateway/configuration-reference`~~ — recovered from `github.com/openclaw/openclaw/blob/main/docs/gateway/configuration-reference.md` (bind config, auth rate limit, provider schema details now confirmed)
- ~~`docs.openclaw.ai/providers/lm-studio`~~ — recovered from `github.com/openclaw/openclaw/blob/main/docs/providers/lmstudio.md`
- `docs.openclaw.ai/api/sessions` — 404; no `docs/api/sessions.md` in repo. Nearest mirrors: `docs/concepts/session.md` (lifecycle) and `docs/cli/sessions.md` (CLI reference).

These gaps are noted inline where they affect the document. The `logs.tail` RPC fills the log-location gap for runtime use.
