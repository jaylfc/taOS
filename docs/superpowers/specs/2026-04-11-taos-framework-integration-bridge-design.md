# TAOS Framework Integration Bridge — design

**Status:** Draft for review
**Date:** 2026-04-11
**Author:** jaylfc
**Supersedes:** the per-framework adapter shape sketched informally in
`docs/design/framework-agnostic-runtime.md` (which remains the load-bearing
rule the bridge enforces)

## TL;DR

Every agent framework in the TAOS catalog reaches a single host-side **TAOS
Bridge** for service discovery, credentials, memory, skills, channels,
sandbox configuration, and approval flows. The framework's container holds
two environment variables (`TAOS_BRIDGE_URL`, `TAOS_AGENT_API_KEY`) and
nothing else of substance. Per-framework integration is a small fork patch
that loads the bridge bootstrap response and configures the framework's
existing native clients (OpenAI, MCP, qmd, SSE) from it. The user manages
everything from the TAOS UI; the framework is a costume the user never has
to dress directly.

The framework-agnostic runtime rule from
`docs/design/framework-agnostic-runtime.md` — *containers hold code, hosts
hold state* — is the rule this design enforces. The bridge is the
mechanism.

## Goals

- **The user never touches a framework config.** Skills, models, channels,
  sandbox, schedules, plugins are all managed in the TAOS UI; the bridge
  translates AgentState into runtime configuration the framework consumes.
- **Memory and per-agent state survive framework swap and container
  rebuild without loss.** Verified by the openclaw → Hermes → openclaw
  round-trip test (Layer 4) on both ARM64 (Orange Pi) and x86_64 (Fedora
  box) before each release.
- **Framework integrations are small.** Adding a framework to the catalog
  is ~80 lines of YAML manifest plus a ~200 LOC fork patch implementing
  the TAOS Bridge client. No per-framework Python in the TAOS controller.
- **Forks are temporary.** Every fork patch is upstream-PR-pending; the
  endgame is every framework graduating to `compliance.tier: verified`
  with no fork.
- **One adapter per framework, not one per concern.** A framework's bridge
  client wires up its native LLM, MCP, memory, and channel adapters from a
  single bootstrap response — not from four separate templated config
  blocks.

## Non-goals

- Replacing the framework's own runtime, scheduler, or agent loop. We
  configure frameworks; we do not wrap their internals.
- Inventing a new wire protocol for chat, tool use, embeddings, or
  channels. Existing standards (Responses API / Chat Completions, MCP,
  qmd, SSE) carry the actual traffic. The bridge is a discovery facade
  over those standards.
- Cross-host live agent migration. Phase 1 pins agents to one host;
  Phase 2 onward considers replication.
- Mid-conversation framework swap. Swap requires the agent to stop and
  restart; this is acceptable and the framework-swap test verifies the
  state-preservation guarantee across that boundary.

## Architecture overview

```
┌──────────────────────────── TAOS host ───────────────────────────────┐
│                                                                       │
│  ┌──────────────┐    ┌────────────────────┐                         │
│  │  TAOS Web UI │───▶│  AgentState store  │                         │
│  │              │    │  data/agents/*.yml │                         │
│  └──────────────┘    └─────────┬──────────┘                         │
│                                │                                    │
│                                ▼ on change                          │
│             ┌────────────────────────────────────┐                  │
│             │  Framework Integration Reconciler  │                  │
│             │  (one host process, generic)       │                  │
│             └─────────┬──────────────────────────┘                  │
│                       │ looks up                                    │
│                       ▼                                             │
│   ┌────────────────────────────────────────────────────────┐       │
│   │  Catalog: app-catalog/agents/{fw}/                     │       │
│   │   • framework-integration.yaml (capabilities, install) │       │
│   │   • fork ref + patch list                              │       │
│   │   • test fixtures                                      │       │
│   └────────────────────────────────────────────────────────┘       │
│                                                                       │
│   ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐  │
│   │   TAOS Bridge    │  │  qmd.service     │  │  taos-skills-mcp │  │
│   │   :6971          │  │  :7832           │  │  :6970           │  │
│   │   discovery +    │  │  per-tenant      │  │  HTTP MCP        │  │
│   │   credential     │  │  dbPath routing  │  │  per-agent       │  │
│   │   facade         │  │                  │  │  filtering       │  │
│   └──────────────────┘  └──────────────────┘  └──────────────────┘  │
│                                                                       │
│   ┌──────────────────┐  ┌──────────────────┐                        │
│   │  litellm proxy   │  │  taos-channels   │                        │
│   │  :4000           │  │  (shared with    │                        │
│   │  team-based      │  │  bridge :6971)   │                        │
│   │  model aliases   │  │  Discord/Slack/… │                        │
│   └──────────────────┘  └──────────────────┘                        │
└───────────────────────────────────────────────────────────────────────┘
                                  │
                                  │  bootstrap (HTTP) + push (SSE)
                                  ▼
              ┌────────────────────────────────────────────┐
              │  Agent container (one per agent)           │
              │  ┌──────────────────────────────────────┐  │
              │  │  Framework + TAOS Bridge adapter     │  │
              │  │  • fetches /agents/{name}/bootstrap  │  │
              │  │  • configures native LLM client      │  │
              │  │  • configures native MCP client      │  │
              │  │  • configures qmd CLI / mcporter     │  │
              │  │  • subscribes to channel events SSE  │  │
              │  └──────────────────────────────────────┘  │
              │  /memory      ← data/agent-memory/X/       │
              │  /workspace   ← data/agent-workspaces/X/   │
              │  /sessions    ← data/agent-sessions/X/     │
              └────────────────────────────────────────────┘
```

**Key flow.** The user toggles a skill in the TAOS UI. The route mutates
`data/agents/{name}.yaml` (the AgentState). The Reconciler reacts: it
loads the catalog's `framework-integration.yaml`, recomputes the bridge
state, calls the bridge's `invalidate(name)`, and the bridge pushes
`config.changed` over SSE to the agent's framework. The framework
re-fetches `/bootstrap` and reconfigures its internal clients. No file
writes inside the container, no process restarts, sub-500ms from click
to availability.

## AgentState — the framework-agnostic data model

Lives at `data/agents/{name}.yaml`, owned by the TAOS UI, consumed by the
Reconciler. Schema (full form, with all optional groups populated):

```yaml
name: research-agent          # immutable, primary key
framework: openclaw           # catalog id
framework_version: 2026.4.4   # pinned (see Forks + pins section)

display:
  color: "#5b8def"
  emoji: 🔍

models:                       # slot-based; framework never sees real model names
  chat:
    id: qwen3-4b-q4           # catalog model id
    temperature: 0.2
  fast:
    id: qwen3-1.7b
    temperature: 0.0
  reasoning:
    id: qwen3-32b
    temperature: 0.4
  embedding:
    id: qwen3-embedding-0.6b
  vision:
    id: qwen2-vl-7b
  stt:
    id: whisper-large-v3-turbo
  routing:
    strategy: latency_first   # latency_first | cost_first | quality_first | manual
    fallback: chat

memory:
  enabled: true
  collections:
    - name: notes
    - name: imports

skills:                       # TAOS-generic skills exposed via MCP gateway
  - web_search
  - file_read
  - file_write
  - memory_search

plugins:                      # framework-specific plugins from the catalog
  - id: playwright-mcp
    enabled: true
    config_ref: secrets://playwright-creds

channels:                     # TAOS-managed; framework never sees credentials
  - taos_id: channel-discord-research-server-general
    permissions: [read, write, react]
  - taos_id: channel-slack-eng-standup
    permissions: [read]

secrets:                      # named refs into data/secrets.db
  - id: github-pat

resources:
  memory_limit: 2GB
  cpu_limit: 2

permissions:
  can_read_user_memory: false
  can_read_agent_memories:
    - name: inbox-agent
      mode: read-only
      collections: ["summaries"]
  can_write_agent_memories: []
  can_send_email: false
  can_use_browser: true

observability:
  tags:
    team: research
    project: model-mesh
    cost_center: rd
    owner: jay
  trace_sample_rate: 1.0
  log_level: info
  emit_to: [dashboard, prometheus]

schedule:
  enabled: true
  jobs:
    - id: morning-digest
      cron: "0 9 * * 1-5"
      task:
        kind: prompt
        prompt: "Summarise overnight emails into MEMORY.md"
        timeout_seconds: 600
      enabled: true
      on_failure: notify
      max_retries: 0

sandbox:
  filesystem:
    readable: [/workspace, /memory]
    writable: [/workspace]
    denied: [/etc, /proc, /sys]
  network:
    mode: allowlist            # allowlist | all | none
    allow: [github.com, huggingface.co, "*.tinyagentos.local"]
    rate_limit_rpm: 600
  subprocess:
    mode: denylist
    denied: [rm, dd, mkfs, sudo]
    timeout_seconds: 30
  resources:
    disk_quota_mb: 1024
    max_processes: 64
    max_open_files: 256
  approval:
    require_for: [subprocess, network_write]
    approver: jay
    timeout_seconds: 300

state:                        # mutable runtime state, written by TAOS
  status: running              # deploying | running | failed | stopped
  container_id: taos-agent-research-agent
  last_deployed_at: 2026-04-11T13:22:01Z
  framework_config_hash: sha256:...
  skills_gateway_id: sgw-default-controller
```

**Three rules guiding the schema:**

1. No file paths. The Reconciler computes them from `name` and the
   host's `data_dir`. Moving an agent between TAOS installs is a
   single-file copy plus a workspace/memory dir copy.
2. Secrets and config refs are opaque references resolved at
   render time by reading `data/secrets.db`. The AgentState file in
   git/backups only carries references.
3. Skills and plugins are separate buckets. *Skills* are TAOS-generic,
   exposed to any framework via MCP. *Plugins* are framework-specific
   extensions from the catalog. The TAOS UI surfaces both in the same
   tab; the bridge knows which bucket each came from.

## Framework Integration Manifest schema (v2)

Lives next to each catalog entry's `manifest.yaml` as
`framework-integration.yaml`. Drives the Reconciler. Declarative-first
with explicit hook points for the rare Tier B escape hatches.

```yaml
schema_version: 2
framework_id: openclaw

source:
  kind: pinned                  # pinned | fork | hard-fork
  version: 2026.4.4             # exact upstream version
  fork:                         # populated when kind == fork
    git: https://github.com/jaylfc/openclaw
    ref: taos-fork
    upstream: https://github.com/openclaw/openclaw
    upstream_version: 2026.4.4
    fork_version: 2026.4.4-taos.1
    patches:
      - name: taos-bridge-adapter
        description: Loads /bootstrap from TAOS_BRIDGE_URL and configures
          openclaw's existing OpenAI, MCP, and qmd clients from it.
          Adds channels.kind="taos-bridge".
        files: [src/taos-bridge.ts, src/channels/index.ts]
        lines_changed: 247
        upstream_pr: https://github.com/openclaw/openclaw/pull/NNN
        pr_status: under-review
        pr_opened: 2026-04-15
  verified_against:
    taos: 0.4.0
    tested_at: 2026-04-11

compliance:
  tier: verified-with-fork      # verified | verified-with-fork | on-ramp-only
  audited_by: jaylfc
  audited_at: 2026-04-11
  audited_against:
    taos: 0.4.0
    framework: 2026.4.4
  guarantees:
    persistence_complete: true
    framework_swap_safe: true
    container_upgrade_safe: true
    secrets_isolation: true
    sandbox_enforced: true
  warnings: []                  # populated only for on-ramp-only tier

# What the framework needs from TAOS — declarative dependencies. The
# bridge supplies actual configuration at runtime. No template rendering
# needed in the common case.
needs:
  llm: openai-responses         # openai-responses | openai-chat | native | none
  memory: qmd                   # qmd | mem0 | sqlite-volume | json-volume
  skills: mcp-http              # mcp-http | mcp-stdio | http-api | none
  channels: sse-bridge          # sse-bridge | none
  approval: webhook             # webhook | none
  sandbox: native-enforced      # native-enforced | host-enforced | none

# Persistence contract — REQUIRED. Every writable in-container path must
# be declared here. The Reconciler refuses to deploy a framework whose
# persistence block is missing or empty. The persistence audit (Layer
# tests) snapshots all writable paths after a sample interaction and
# fails if anything important landed outside these mounts.
persistence:
  memory:
    kind: qmd-cli               # qmd-cli | qmd-http | mem0-compat | sqlite-volume | json-volume
    mount: /memory
    host_subdir: agent-memory/{name}/
    writable: true
  workspace:
    mount: /workspace
    host_subdir: agent-workspaces/{name}/
    writable: true
  sessions:
    mount: /sessions
    host_subdir: agent-sessions/{name}/
    writable: true
  plugin_state:
    mount: /var/lib/openclaw
    host_subdir: agent-plugin-state/{name}/openclaw/
    writable: true
  additional_paths: []

capabilities:
  multi_model: true
  hot_reload:
    kind: bridge-poll           # bridge-poll | file-watch | sighup | api | restart-only
    interval_seconds: 60
    push_supported: true
    fallback: restart
  metrics:
    format: prometheus
    endpoint: /metrics
  observability:
    structured_logs: true
    trace_format: otlp

install:
  base_image: debian:bookworm-slim
  steps:
    - apt: [nodejs, npm, git, sqlite3, ca-certificates]
    - run: "npm install -g @jaylfc/openclaw@2026.4.4-taos.1"
    - run: "npm install -g qmd@2.0.1 mcporter"
    - mkdir: [/etc/openclaw, /var/log/openclaw, /var/lib/openclaw]

runtime:
  command: ["openclaw", "serve", "--bridge"]
  working_dir: /etc/openclaw
  env:
    TAOS_BRIDGE_URL: "http://${TAOS_HOST}:6971"
    TAOS_AGENT_API_KEY: "${AGENT_API_KEY}"
    OPENCLAW_LOG_FORMAT: json
  user: openclaw
  ports: [8080, 9090]

health:
  kind: http
  url: "http://localhost:8080/health"
  initial_delay_seconds: 5
  interval_seconds: 10
  failure_threshold: 3

reload:
  kind: bridge-poll
  interval_seconds: 60
  push_supported: true
  fallback: restart

# Tier B escape hatches — optional. openclaw needs none.
hooks: {}

# Declarative migration adapters — supports automated migration from
# other formats into qmd-backed TAOS storage.
data_migration:
  from_self:
    description: Read this framework's existing index for migration
    reader:
      kind: sqlite
      mount: /memory/index.sqlite
      query: |
        SELECT c.hash, c.doc, c.created_at, d.collection, d.path, d.title
        FROM content c JOIN documents d ON d.hash = c.hash AND d.active = 1
      mapping:
        text_field: doc
        timestamp_field: created_at
        title_template: "{{ title }}"
        collection_field: collection
  to_qmd:
    description: openclaw is qmd-backed; migration to other qmd-backed
      frameworks is a copy of the SQLite file
    writer:
      kind: noop
      reason: same storage layer
  from_external:
    - id: mem0-export
      description: Import a mem0 JSON export
      reader:
        kind: jsonl
        path_pattern: "*.jsonl"
        mapping:
          text_field: memory
          timestamp_field: created_at
          collection: imports

tests:
  smoke:
    - name: "renders cleanly with minimal AgentState"
      input: tests/fixtures/minimal-agent.yaml
      expect_renders: true
    - name: "renders cleanly with full AgentState"
      input: tests/fixtures/full-agent.yaml
      expect_renders: true
  golden:
    - name: minimal
      input: tests/fixtures/minimal-agent.yaml
      golden_output: tests/golden/minimal-agent.bootstrap.json
    - name: full
      input: tests/fixtures/full-agent.yaml
      golden_output: tests/golden/full-agent.bootstrap.json
```

**Per-framework footprint:** ~80 lines of YAML manifest plus a ~200 LOC
fork patch implementing the bridge client. No Python in TAOS core.

## TAOS Bridge — discovery + credential facade

The bridge is one HTTP+SSE service on the TAOS host (`taos-bridge.service`,
default port 6971). It does **not** invent a new wire protocol — wire
formats stay standards-compliant (Responses API / Chat Completions for
LLM, MCP for skills, qmd for memory, SSE for channel events). The
bridge changes how the framework finds and authenticates against them.

### Bootstrap response

```
GET https://taos:6971/agents/{name}/bootstrap
Authorization: Bearer ${TAOS_AGENT_API_KEY}

→ 200 OK
{
  "schema_version": 1,
  "agent": {
    "name": "research-agent",
    "display_name": "Research Agent"
  },
  "services": {
    "llm": {
      "kind": "openai-responses",
      "base_url": "http://taos:4000/v1",
      "api_key": "sk-litellm-team-research-agent-...",
      "responses_endpoint": "/v1/responses",
      "chat_completions_endpoint": "/v1/chat/completions",
      "models": {
        "chat": "taos-chat",
        "fast": "taos-fast",
        "reasoning": "taos-reasoning",
        "embedding": "taos-embedding",
        "vision": "taos-vision",
        "stt": "taos-stt"
      },
      "routing": { "strategy": "latency_first", "fallback": "chat" }
    },
    "memory": {
      "kind": "qmd",
      "qmd_server": "http://taos:7832",
      "db_path": "/memory/index.sqlite",
      "additional_paths": [
        { "path": "/memory/grants/inbox-agent.sqlite", "name": "inbox-agent", "readonly": true }
      ],
      "sessions_dir": "/sessions",
      "embedding_model": "taos-embedding"
    },
    "skills": {
      "kind": "mcp-http",
      "url": "http://taos:6970/mcp/research-agent",
      "transport": "http"
    },
    "plugins": [
      { "id": "playwright-mcp", "kind": "mcp-http", "url": "http://taos:6970/plugins/playwright-mcp/research-agent" }
    ],
    "channels": {
      "kind": "sse-bridge",
      "events_url": "http://taos:6971/agents/research-agent/events",
      "send_url": "http://taos:6971/agents/research-agent/send"
    },
    "approval": {
      "kind": "webhook",
      "url": "http://taos:6969/api/agents/research-agent/elevated-approval"
    }
  },
  "sandbox": {
    "filesystem": { "readable": ["/workspace", "/memory"], "writable": ["/workspace"], "denied": ["/etc", "/proc", "/sys"] },
    "network":    { "mode": "allowlist", "allow": ["github.com", "huggingface.co"], "rate_limit_rpm": 600 },
    "subprocess": { "deny": ["rm", "dd", "mkfs", "sudo"], "timeout_seconds": 30 }
  },
  "observability": {
    "log_level": "info",
    "trace_sample_rate": 1.0,
    "tags": { "team": "research", "project": "model-mesh" }
  },
  "config_version": "sha256:abc...",
  "next_check_after_seconds": 60
}
```

The framework's bridge adapter reads `TAOS_BRIDGE_URL` and
`TAOS_AGENT_API_KEY`, GETs `/bootstrap`, configures its native clients
from the response, and subscribes to the SSE stream at
`{TAOS_BRIDGE_URL}/agents/{name}/events` for `config.changed`
notifications. That's the entire integration surface.

### Push reload via SSE

When AgentState changes:

```
SSE stream from bridge → framework
event: config.changed
data: { "config_version": "sha256:def...", "fields_changed": ["models", "skills"] }
```

The framework re-fetches `/bootstrap` and reconfigures only the affected
clients. The 60s poll loop is the safety net for dropped SSE connections.

### Channel bridge inside the same service

`/agents/{name}/events` and `/agents/{name}/send` ride on the same TAOS
Bridge process. The framework never sees an upstream channel credential.
TAOS owns all upstream channel clients (Discord, Slack, Telegram, etc.)
and presents normalized events:

```json
{
  "event": "message.received",
  "channel": {
    "taos_id": "channel-discord-research-server-general",
    "kind": "discord",
    "display_name": "research-server #general",
    "permissions": ["read", "write", "react"]
  },
  "from": { "taos_id": "user-jay", "display_name": "Jay", "is_bot": false },
  "message": {
    "taos_id": "msg-abc123",
    "content": "Can you summarise yesterday's meeting?",
    "attachments": [],
    "thread_id": null,
    "ts": "2026-04-11T14:23:09Z"
  }
}
```

Outbound sends:

```
POST http://taos:6971/agents/research-agent/send
Authorization: Bearer ${TAOS_AGENT_API_KEY}
{
  "channel": "channel-discord-research-server-general",
  "in_reply_to": "msg-abc123",
  "content": "Yesterday's meeting covered…"
}
```

The bridge resolves the TAOS channel id to its real upstream channel,
looks up the bot token from `data/secrets.db`, and dispatches via the
Discord client. **The framework never sees `botToken`, `serverId`, or any
upstream identifier.**

### LLM via LiteLLM team aliases

Each TAOS agent is a LiteLLM team. Each team has its own `model_aliases`
mapping the slot names (`taos-chat`, `taos-fast`, `taos-reasoning`,
`taos-embedding`, etc.) to whatever real backend the AgentState
currently selects. The agent's container holds a LiteLLM virtual key
bound to its team. When the user changes a model in the TAOS UI, the
Reconciler updates the team's aliases via LiteLLM's `/team/update` API
— no SIGHUP, no framework restart, the next request lands on the new
backend.

The bridge advertises both `responses_endpoint` and
`chat_completions_endpoint` so frameworks can pick the API surface
appropriate for their adapter. Responses API is preferred for new
adapters (structured tool use, reasoning, stateful threading).

### Bridge protocol versioning

Schema version is `1`. Additive changes (new optional fields) bump a
minor (e.g. `1.1`); old framework clients ignore them. Required-field
changes bump a major (`v2`); each fork has to update its bridge client
and re-test. The protocol is intentionally additive — we avoid major
bumps.

## Reconciler — apply pipeline and reload semantics

Lives at `tinyagentos/framework_integrations/reconciler.py`. The single
host-side process that turns AgentState changes into running framework
state. Event-driven, not polled.

### Triggers

1. **API mutation.** Any TAOS route that mutates `data/agents/{name}.yaml`
   calls `reconciler.apply(name)` after the file write. Synchronous; the
   route returns the reconcile result.
2. **File watch** on `data/agents/`. inotify catches out-of-band edits
   (manual edit, restored backup, sync from another box). Debounced
   500ms.
3. **Boot reconcile.** On TAOS startup, walks `data/agents/*.yaml` and
   reconciles every agent whose `state.framework_config_hash` doesn't
   match what the bridge currently serves.

All three funnel into `reconciler.apply(agent_name) -> ReconcileResult`.
Per-agent serialization via asyncio.Lock; cross-agent parallelism
bounded by a semaphore (default 4).

### Apply pipeline

```
1.  Load AgentState from data/agents/{name}.yaml
2.  Load FrameworkIntegration manifest from app-catalog/agents/{framework}/
3.  Resolve secrets (refs → plaintext, in-memory only)
4.  Build LiteLLM team_id + model_aliases via POST /team/update
5.  Run pre_render hook if declared (Tier B only)
6.  Compute the bridge bootstrap response shape from AgentState
7.  Compute sha256 of the bootstrap → new config_version
8.  Compare to state.framework_config_hash
     ├─ no change: short-circuit, success
     └─ change: continue
9.  Update bridge state (cache the new bootstrap response server-side)
10. Push config.changed SSE notification to the agent's open subscription
11. Wait for the framework's health check to confirm reload (or timeout)
12. Run post_apply hook if declared (Tier B only)
13. Update state.framework_config_hash + last_deployed_at
14. Save AgentState back to disk
```

### Reload strategies (per `reload.kind`)

| `reload.kind` | Behavior |
|---|---|
| `bridge-poll` (preferred — openclaw uses this) | Bridge updates its cached bootstrap state and pushes `config.changed` over SSE. Framework re-fetches, reconfigures, no file write. Poll loop is the safety net for dropped SSE. |
| `file-watch` | Atomic write to a watched config file (rare; only for frameworks that can't be patched to consume the bridge directly). |
| `sighup` | Atomic write, then `kill -HUP <pid>`. |
| `api` | Atomic write, then HTTP POST to a declared reload endpoint. |
| `restart-only` | Container restart. Last resort, fallback for any of the above when their reload fails twice. |

### Failure modes

- **Render failure (steps 5–7):** AgentState revert from backup, user
  sees error, container untouched.
- **LiteLLM team update failure (step 4):** Aliases never applied,
  AgentState reverted, the change never reaches the framework.
- **Bridge state update failure (step 9):** Old bootstrap remains live,
  AgentState reverted.
- **Reload failure (step 10–11):** Bridge state reverted, framework
  re-fetches the previous-good config on next poll, container is left
  running its previous-good state.
- **post_apply hook failure (step 12):** Treated as warning, not hard
  error. The change is live; the follow-up failed.

`state.framework_config_hash` is only updated after step 11 succeeds, so
boot reconcile detects any agent whose container drifted from its
declared state.

### What the Reconciler does NOT do

- **Schedule jobs.** Cron jobs from `AgentState.schedule.jobs[]` are
  managed by the existing TAOS scheduler. The Reconciler renders them
  into the bridge bootstrap so the framework knows about them, AND
  registers them with TAOS scheduler for host-side enforcement and
  audit. The Reconciler is the source of truth; both consume.
- **Manage container lifecycle.** Container create/destroy stays in
  `tinyagentos/deployer.py`. The Reconciler operates on already-deployed
  containers.
- **Talk to skill MCP servers directly.** The bridge advertises the
  gateway URL; the framework connects directly.

## TAOS Skills MCP Gateway

Separate process: `taos-skills-mcp.service` on `:6970`. Stateless
except for in-memory caches. Movable between machines for cluster
dispatch.

### Wire shape

- **Transport: HTTP + SSE.** Stdio is single-tenant by definition.
- **Identity: per-agent URL path.** `http://taos:6970/mcp/{agent_name}`.
  The framework sees this as a normal MCP server URL; the gateway
  extracts the agent name from the path on every request and filters
  responses against AgentState.
- **Auth (cluster mode):** TAOS auth token in URL or header. Same
  middleware as the rest of the API.

### Method behavior

| MCP method | Gateway behavior |
|---|---|
| `initialize` | Returns server info; caches the agent's skill set for the session. |
| `tools/list` | Loads AgentState for the path's agent, builds the tool list from `skills[] + plugins[*].tools`, returns the filtered set. The agent never sees a tool that isn't in its AgentState. |
| `tools/call` | Looks up the tool, dispatches to (a) imported core skill code for `web_search`, `file_read`, etc, or (b) the upstream plugin MCP server for plugin tools. Pipes the result back, SSE-streams partial output where supported. |
| `notifications/tools/list_changed` | Emitted when AgentState changes the skill set, driven by Reconciler invalidation. The framework re-fetches `tools/list`. |
| `resources/list` / `resources/read` | Delegated to whichever component owns the resource. Same per-agent filtering. |

### Core skills vs plugin skills

The gateway is a filtering proxy in front of two sources:

1. **Core skills** — refactored from the existing
   `tinyagentos/routes/skill_exec.py` into a shared
   `tinyagentos/skills/core/` Python package the gateway imports
   directly. Skills run in the gateway's process, not the controller's,
   so heavy operations (`code_exec`, browser sessions) move with the
   gateway when it's deployed to a beefier host.
2. **Plugin skills** — each plugin from `app-catalog/plugins/*` is its
   own MCP server. The gateway proxies through to it, scoped to the
   calling agent's plugin grants. Plugin authors write a normal MCP
   server; the gateway makes it multi-tenant.

### Gateway registry for cluster mode

Lives in `data/skills_mcp_gateways.db` on the TAOS controller. Each
gateway registers on startup (`POST /api/skills-mcp-gateways/register`)
declaring host, port, capabilities, and `max_plugins`. The Reconciler
picks a gateway when assigning to an agent, based on:

- Health
- Capacity (`current_load < max_plugins`)
- Plugin class support (Playwright gateway for browser-using agents,
  light gateway for text-only agents)
- Sticky preference (`AgentState.runtime.skills_gateway_id`)

If the agent's previously-assigned gateway is unreachable, the
Reconciler picks a new one and pushes a bridge update. From the agent's
perspective the MCP session reconnects to the new URL.

## Memory routing

Builds on the existing per-tenant qmd serve infrastructure
(`qmd.service` on `:7832` with `dbPath` routing on every endpoint).

### Persistence kinds

| `persistence.memory.kind` | Behavior |
|---|---|
| `qmd-cli` | Bind-mount `data/agent-memory/{name}/index.sqlite` at the declared mount path. Inject `QMD_SERVER=http://qmd-host:7832` so the in-container `qmd` CLI routes embedding/rerank/expand requests over HTTP to the host qmd.service. Storage is local SQLite. |
| `qmd-http` | No bind-mount. The framework reads/writes purely over the qmd HTTP API with the dbPath header on every call. Used when filesystem access to the index is undesirable. |
| `mem0-compat` | A small mem0-compatible HTTP shim translates mem0 API to qmd serve calls. Future work; ships in Phase 3. |
| `sqlite-volume` | Framework uses its own SQLite-based memory format. Bind-mount the directory; framework owns the schema. **No qmd integration** — these are temporary on-ramps with required migration adapters. |
| `json-volume` | Same as `sqlite-volume` for JSON files. |

The first three (`qmd-cli`, `qmd-http`, `mem0-compat`) all funnel writes
into the same per-agent SQLite at
`data/agent-memory/{name}/index.sqlite`. Changing a framework that uses
any of these never costs the user their memory.

### Cluster scaling

Each worker host runs its own `qmd.service`. Each agent is pinned to a
host. Reads and writes are local; cross-agent / cross-host queries use
the controller's federated query path. Phase 1 does not auto-failover
agents on host loss — the agent stays offline until its host returns.
Phase 2 evaluates litestream for SQLite streaming replication.

### Cross-agent grants

```yaml
permissions:
  can_read_user_memory: true
  can_read_agent_memories:
    - name: inbox-agent
      mode: read-only
      collections: ["summaries"]
  can_write_agent_memories: []
```

The Reconciler resolves grants at apply time. For each grant, it
injects an additional `dbPath` the agent's qmd CLI and gateway memory
tools can query, scoped to the listed collections. Each agent's index
remains its own file — grants are read-time projections, not
duplications. Cross-agent writes are gated, audited to `data/audit.db`,
and default to forbidden.

## Migration runner — fork-first policy

For every catalog framework, in order of preference:

1. **Native qmd-cli or qmd-http** (no fork needed).
2. **Forked to add qmd** — for frameworks with subpar or no memory,
   this is a strict feature upgrade for users.
3. **`sqlite-volume` / `json-volume` as a temporary on-ramp** — only
   when (1) and (2) aren't ready. Required to ship a migration
   adapter into a qmd-backed alternative; not a permanent tier.

### Migration runner

`tinyagentos/framework_integrations/migrations.py`. Reads any supported
source format and writes through to qmd via the existing `/ingest`
endpoint. Same tool used in two directions: inbound (third-party →
TAOS qmd) and outbound (TAOS qmd → third-party).

Migration adapters are declarative, declared in
`framework-integration.yaml > data_migration`:

```yaml
data_migration:
  from_self:
    reader: { kind: sqlite, mount: ..., query: ..., mapping: ... }
  to_qmd:
    writer: { kind: qmd-http, endpoint: /ingest, collection: imports }
  from_external:
    - id: mem0-export
      reader: { kind: jsonl, ... }
    - id: chatgpt-conversations-export
      reader: { kind: json-dir, path_pattern: "conversations/*.json", ... }
```

Three things this enables:

1. **TAOS UI flow.** The "Add agent" wizard sees the framework's
   `from_external` list and offers an "Import existing memory from..."
   step.
2. **Most adapters are pure config.** `kind: sqlite + query + mapping`
   covers ~80% of cases. A `kind: python-callable` escape hatch handles
   the rest (binary formats, custom encodings, multimodal extraction).
3. **Migration is the same operation as ingest.** No separate write
   path — the runner calls qmd serve's `/ingest`. Tested once, works
   everywhere.

## Telemetry and opt-in metrics

Optional, off by default, fully transparent.

### Hard rules

1. **Opt-in, not opt-out.** Default OFF. The user is shown the full
   schema during first-run setup and chooses.
2. **No identifying data, ever.** No user names, no agent names, no
   chat content, no model outputs, no API keys, no file paths. Install
   ID is a random UUID generated locally on first opt-in.
3. **Aggregated counts and categories only.**
4. **Schema is versioned and public.** Documented in
   `docs/telemetry-schema.md`. `taos telemetry dump` shows the next
   payload that would be sent.
5. **Endpoint is self-hostable.** The receiver code ships in the same
   repo. Users can point at `localhost`, their own collector, or
   nowhere.
6. **Revocable instantly.** Toggling off stops the next send and wipes
   the local install ID.
7. **No chat / content / model outputs is enforced at the schema layer.**
   The collector rejects payloads with unknown fields.

### v1 schema (the entire payload)

```jsonc
POST https://telemetry.tinyagentos.com/api/v1/heartbeat
{
  "schema_version": 1,
  "install_id": "550e8400-...",
  "taos_version": "0.4.0",
  "first_seen_at": "2026-01-15",
  "uptime_days": 87,
  "host": { "os": "linux", "arch": "aarch64", "hardware_tier": "arm-npu-16gb", "ram_gb": 16, "cpu_cores": 8 },
  "cluster": { "worker_count": 1, "worker_oses": ["linux"], "total_ram_gb": 32 },
  "agents": {
    "total": 4,
    "by_status": { "running": 3, "stopped": 1, "failed": 0 },
    "by_framework": { "openclaw": 2, "smolagents": 1, "langroid": 1 }
  },
  "models": {
    "downloaded": 7,
    "by_backend": { "rkllama": 3, "ollama": 2, "llama-cpp": 2 },
    "by_capability": { "chat": 4, "embedding": 1, "vision": 1, "stt": 1 }
  },
  "skills": {
    "total_assigned": 12,
    "by_id": { "web_search": 4, "file_read": 3, "memory_search": 4, "code_exec": 1 }
  },
  "plugins": { "installed": 3, "by_id": ["playwright-mcp", "exa-mcp-server", "context7-mcp"] },
  "torrent": { "enabled": true, "seeding_count": 5, "seeding_total_gb": 22 },
  "features": {
    "litellm_proxy": true, "qmd_service": true, "skills_mcp_gateway": true,
    "scheduler_jobs_enabled": false, "kiosk_mode": false, "tailscale": true
  }
}
```

### Public dashboard

`tinyagentos.com/community` shows the aggregate stats: install count
over time, framework popularity, hardware tier distribution, total
models downloaded, total agents running. Updated nightly. **The
dashboard is the answer to "what do you do with the data" — it's all
visible.**

### Self-hosted collector

The same collector code ships in `scripts/telemetry-collector/` as a
systemd unit. Organizations can run their own collector and never send
anything to tinyagentos.com.

## Forks + pins policy

### Fork lifecycle

```
[upstream]              [taos-fork]              [catalog]
    │                       │                       │
    │ fork at v2026.4.4 ───▶│                       │
    │                       │                       │
    │                       │ apply patches:        │
    │                       │  - taos-bridge-adapter│
    │                       │  - persistence-mounts │
    │                       │                       │
    │                       │ build, test ─────────▶│ tier: verified-with-fork
    │                       │                       │
    │ ◀── PR opened ───────│                       │ status: pending-upstream-pr
    │                       │                       │
    │ ── PR merged ───────▶ │                       │
    │                       │                       │
    │ release v2026.5.0 ──▶ │                       │
    │                       │ verify upstream is    │
    │                       │ now TAOS-compatible   │
    │                       │                       │
    │                       │ ─── pin upstream ───▶ │ tier: verified
    │                       │ ─── retire fork ────▶ │ no fork ref
```

The fork is the proof-of-concept; the upstream PR is the goal;
graduation to `verified` is the signal we're done with it. **The
endgame is zero forks.**

### Patch discipline

Every patch in a fork is:

- **Small.** Over ~500 LOC, we split it.
- **Standalone.** No patch may depend on other patches. Each is
  independently mergeable upstream.
- **Generic.** A patch must work even for someone not running TAOS.
  The TAOS bridge adapter pitches as "load my config from any HTTP
  endpoint that returns this schema."

### Bridge Adapter Reference Implementations

To keep fork patches consistent, we ship two reference implementations
of the TAOS Bridge client, vendored into each fork:

- **`taos-bridge-client-ts`** — TypeScript. Used by openclaw and any
  Node-based framework.
- **`taos-bridge-client-py`** — Python. Used by smolagents, langroid,
  langchain-based frameworks.

Each is small, dependency-free, and exposes typed accessors:
`bridge.llm()`, `bridge.memory()`, `bridge.skills()`, `bridge.channels()`.
The fork patch for a framework is then thin glue: import the client,
configure framework adapters from its accessors.

### Version pinning

- Pin to a specific upstream version, never a branch tip.
- Pin updates are deliberate (review, re-test, security audit).
- Forks pin to the same upstream version as their non-fork base. Fork
  versions use a `-taos.N` suffix (`2026.4.4-taos.1`).

### Audit cadence

Nightly CI job:

- For every fork, fetch upstream's latest tag
- Compare against `source.upstream_version`
- For new versions, open a "version bump candidate" issue automatically
- For frameworks with open upstream PRs, scrape the PR status and
  update `pr_status` in the manifest
- Surface results in the public dashboard at
  `tinyagentos.com/community/frameworks`

## Testing strategy

Six layers, gated by explicit runtime targets.

### Layer 1 — Unit (sub-second)

Pure functions, no I/O. AgentState parsing, manifest validation,
Reconciler render path, slot resolution, migration readers, telemetry
schema, bridge protocol clients, catalog audit.

**Gate:** every PR. Target: < 30s.

### Layer 2 — Integration (~1 min)

Real services, in-process or as test fixtures. Real TAOS controller +
real qmd.service + real LiteLLM + real Skills MCP gateway + real
Channels service. The framework is mocked by `tests/fake_framework.py`.

Tests cover: Reconciler.apply() end-to-end, LiteLLM team key creation
+ alias resolution, qmd ingest + search round-trip per-agent, Skills
MCP gateway tool filtering, Channel bridge SSE delivery, bridge
config.changed push, approval webhook round-trip.

**Gate:** every PR for affected paths. Target: < 90s.

### Layer 3 — Per-framework smoke (~5 min per framework)

Real container, real framework. One per catalog framework. Smoke =
deploy → chat turn → memory ingest → skill call → tear down.

**Gate:** every PR that touches a framework's manifest, fork, or
template. Full matrix nightly.

### Layer 4 — Framework swap (the load-bearing one, ~15 min)

The cross-framework portability proof. Deploy A, populate state, swap
to B, verify everything survives, swap back to A, verify again. **The
openclaw → Hermes → openclaw round-trip is the canonical Layer 4 test
and the public milestone.**

**Gate:** nightly + pre-release. Pre-release: must pass for every
framework pair before a TAOS version ships.

### Layer 5 — Container upgrade (~10 min)

Rebuild the container from a fresh image. Verify every per-agent piece
of state survives.

**Gate:** weekly + pre-release.

### Layer 6 — Novice user UX simulation

A Playwright-driven Claude Haiku agent loaded with a beginner persona
("you are a hobbyist who has never used an AI agent platform; here is a
task — install and configure a research agent that watches your inbox")
attempts each user-facing flow. Produces a friction report after each
run: clicks made, dead-ends hit, error messages encountered, places it
gave up.

**Haiku is the right oracle precisely because it can't reason its way
past bad UX.** If Haiku gets stuck, a human absolutely will.

Personas cover: Fresh Install, First Agent Deploy, Add a Skill, Swap
Framework, Configure Channel, Recover from Container Crash, Read the
Friction Report (recursive: can a beginner understand the friction
report?).

Friction reports surface (anonymized) on
`tinyagentos.com/community/ux` so contributors can see which screens
are bleeding.

**Gate:** nightly. Failures don't block PRs but produce open issues
labeled `ux-friction` which the catalog roadmap consumes alongside
telemetry signals.

### Persistence audit (catalog acceptance gate)

When a new framework integration PR opens:

1. Deploy a fresh agent on the new framework
2. Run Layer 4 with the new framework as both endpoints
3. Snapshot all writable paths inside the container
4. Diff against `persistence.*` paths declared in the manifest
5. **Any writable file outside the declared mounts is a hard fail.** PR
   cannot merge until either the path is added to `persistence:` or
   the framework's fork patch redirects it.

### Test environment

- **CI runner 1:** ARM64 Orange Pi 5 Plus mirror — same NPU, rkllama,
  qmd.service. Primary target.
- **CI runner 2:** x86_64 Fedora box with NVIDIA GPU + privileged LXC —
  cross-arch validator. Secondary target. Runs Layer 3 and Layer 4
  nightly.
- **Test data isolation:** each test gets its own `data_dir` under
  `/tmp/taos-test-{uuid}/`. No shared state between tests.

## openclaw worked example

### Catalog layout

```
app-catalog/agents/openclaw/
├── manifest.yaml
├── framework-integration.yaml       # the v2 manifest above
├── tests/
│   ├── fixtures/
│   │   ├── minimal-agent.yaml
│   │   └── full-agent.yaml
│   └── golden/
│       ├── minimal-agent.bootstrap.json
│       └── full-agent.bootstrap.json
└── docs/notes.md
```

### Fork patch — what gets shipped to `github.com/jaylfc/openclaw`

One file: `src/taos-bridge.ts` (~200 LOC). It:

- Reads `TAOS_BRIDGE_URL` and `TAOS_AGENT_API_KEY` from env
- Fetches `/agents/{name}/bootstrap` on startup
- Configures openclaw's existing OpenAI provider with `services.llm.base_url + api_key`
- Configures openclaw's existing MCP client with `services.skills.url + services.plugins[*].url`
- Configures openclaw's existing qmd integration with `services.memory.qmd_server` and bind-mount path
- Subscribes to `services.channels.events_url` for SSE events
- Posts to `services.channels.send_url` for outgoing messages
- Calls back to `services.approval.url` when sandbox triggers
- Listens for SSE `config.changed` and re-runs the configure step

Plus a small new file `src/channels/taos-bridge.ts` that registers as a
normal openclaw channel adapter (`channels.kind = "taos-bridge"`).

**That is the entire openclaw integration.** No template, no slot map,
no Jinja2.

### Reload sequences

**Skill toggle:**

```
1. PATCH /api/agents/research-agent/skills
2. AgentState YAML updated, skills += [code_exec]
3. Reconciler.apply("research-agent")
4. Reconciler invalidates the bridge cache for research-agent
5. Bridge emits SSE config.changed to research-agent's open subscription
6. openclaw bridge adapter re-fetches /bootstrap, sees new mcp config
7. openclaw mcp client refreshes tools/list, code_exec available
8. ZERO file writes inside the container, ZERO process restarts
9. Total time: < 200ms from click to availability
```

**Model swap:**

```
1. PATCH /api/agents/research-agent/models
2. AgentState YAML updated, models.chat.id = qwen3-32b
3. Reconciler.apply("research-agent")
4. POST /team/update on LiteLLM with new model_aliases.taos-chat
5. LiteLLM applies the change live (no SIGHUP needed)
6. Bridge bootstrap response shape unchanged (same alias name "taos-chat")
7. config_version unchanged → no SSE notification needed
8. Total time: < 100ms, no openclaw restart, no MCP reconnect
```

**Channel addition:**

```
1. POST /api/agents/research-agent/channels
2. User uploads bot token, stored in data/secrets.db as discord-creds
3. AgentState updated, channels += [{taos_id: ..., permissions: [...]}]
4. Reconciler.apply("research-agent")
5. Bridge state updated, config.changed pushed
6. openclaw bridge adapter re-fetches /bootstrap, sees new channels
7. openclaw subscribes to the additional event stream
8. Channel goes live without container restart
9. Total time: < 500ms
```

## Rollout plan

Three phases gated by the openclaw → Hermes → openclaw round-trip.

### Phase 1 — Foundation

**Goal:** make a single openclaw agent fully driven by the TAOS Bridge,
end-to-end, on the Orange Pi.

**Deliverables:**

1. `tinyagentos/framework_integrations/` package — `agent_state.py`,
   `manifest.py`, `reconciler.py`, `migrations.py`
2. `taos-bridge.service` systemd unit + Python implementation
3. `taos-skills-mcp.service` systemd unit + implementation, with core
   skills moved into `tinyagentos/skills/core/`
4. `taos-channels.service` systemd unit (Discord only in Phase 1)
5. openclaw fork at `github.com/jaylfc/openclaw` with bridge adapter
   patch
6. Catalog entry update for openclaw — `framework-integration.yaml`,
   fixtures, golden files
7. Reconciler integration in routes
8. Test layers 1, 2, 3 for openclaw, green
9. Documentation: this design doc, the bridge protocol spec
   (`docs/protocols/taos-bridge-v1.md`), the contributor guide
   (`docs/runbooks/add-a-framework.md`)

**Acceptance gate:** A user can deploy an openclaw agent in TAOS, chat
with it, ingest a file into its memory, do semantic search, call a
skill, receive a Discord message, send a Discord reply — all driven
entirely by AgentState mutations, with no manual config editing. The
openclaw container has only `TAOS_BRIDGE_URL` and `TAOS_AGENT_API_KEY`
as env vars and a single bootstrap stub file.

Estimated scope: ~3-5k LOC Python + ~250 LOC TypeScript fork patch +
~200 lines YAML. ~2-3 weeks.

### Phase 2 — Public milestone (openclaw ↔ Hermes round-trip)

**Goal:** the framework swap demo. Hermes is added as the second
framework integration. The openclaw → Hermes → openclaw round-trip
passes Layer 4 on both ARM64 (Orange Pi) and x86_64 (Fedora box).

**Deliverables:**

1. Hermes fork + bridge adapter
2. Catalog entry, manifest, fixtures
3. Layer 4 round-trip test wired into CI nightly
4. Cross-arch test runner on the Fedora box
5. Layer 6 Haiku UX simulator with the personas above
6. Public artifacts:
   - GitHub release notes
   - Blog post on tinyagentos.com
   - Short video / GIF of the swap with no data loss
   - Social posts (HN, Lobsters, r/LocalLLaMA, r/selfhosted)

**Acceptance gate:** Layer 4 round-trip test passes 10 consecutive
nightly runs across both architectures. Layer 6 Haiku UX runs land in
a friction report dashboard. Public announcement after gate passes.

Estimated scope: ~2 weeks.

### Phase 3 — Catalog expansion + on-ramp tier

**Goal:** bring the rest of the catalog onto the bridge.

**Deliverables (in priority order, telemetry-driven once available):**

1. smolagents integration
2. langroid integration
3. openai-agents-sdk integration
4. Remaining \*claw frameworks
5. Plugin catalog onboarding to the Skills MCP gateway
6. Slack + Telegram channel adapters
7. Multi-agent grants UI
8. mem0-compat HTTP shim

**Acceptance gate:** at least 4 frameworks at `verified` or
`verified-with-fork`, Layer 4 passing pairwise across them, persistence
audit gate enforced on all catalog merges.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| **Upstream rejects bridge adapter PR.** | Fork stays indefinitely. Patch is < 500 LOC and re-applies cleanly across upstream version bumps. Catalog tier becomes "verified-with-fork" without ever transitioning to "verified" — acceptable. |
| **Bridge protocol v1 turns out to be incomplete.** | Schema is additive — new fields land as `1.x` and old clients ignore them. Major v2 only if a required-field change is unavoidable. |
| **Per-agent containers eat too much RAM at home-server scale.** | Phase 3 introduces an opt-in "lightweight mode" where multiple agents on the same framework can share a container via the framework's native multi-tenancy. Off by default to preserve framework-swap. |
| **Container reload feels laggy.** | Bridge push notifications target sub-500ms for skill toggles + model swaps (no container restart). Container restart reserved for things that genuinely require it. |
| **Fedora cross-arch box is a single point of failure.** | Layer 4 also runs against the Orange Pi. The Fedora run is supplementary, not gating. |
| **Catalog forks fall behind upstream.** | Nightly catalog audit opens version bump candidate issues automatically. Forks more than 2 upstream releases behind get flagged in the dashboard. |
| **Telemetry opt-in rate is too low to drive prioritization.** | Catalog roadmap also accepts user-submitted prioritization signals (GitHub issue upvotes, install requests in the UI). Telemetry is one input among several. |
| **A framework requires Tier B code-first FIM.** | Tier B exists for exactly this. Documented in the bridge protocol spec; we accept some frameworks need real Python glue. |

## Success criteria

The framework adapter design is **done** when:

1. Three or more frameworks at `verified` or `verified-with-fork` with
   the Layer 4 round-trip test passing pairwise
2. A new contributor can add a framework to the catalog by writing only
   the integration manifest, the fork patch, and test fixtures — no
   changes to TinyAgentOS core
3. The user's experience in the TAOS UI is identical regardless of
   which framework they pick (modulo capabilities the framework
   genuinely doesn't support, surfaced via the capabilities matrix)
4. The persistence audit gate has prevented at least one PR from merging
   because of an undeclared write path
5. The public dashboard shows: `Frameworks: N verified, M
   verified-with-fork, 0 on-ramp-only`
6. Telemetry (if users opt in) shows multi-framework adoption — at
   least 3 frameworks accounting for > 5% of agents each

## Deferred to v2

- Cross-host memory replication (litestream / rsync streaming) for
  failover
- Live agent migration between hosts under load
- Multiple Skills MCP Gateway instances with placement scheduling
- Multi-agent shared containers (lightweight mode)
- Framework integration SDK for community contributors (a `taos
  framework init` scaffolder)
- Mid-conversation framework swap
- Per-agent rate limiting and budgets surfaced in the UI
- **Cluster-wide scheduler aggregation.** The Phase 1 scheduler is
  single-host: BackendCatalog probes only the controller's local
  backends, and the Activity widget shows local resources only.
  Phase 2 cluster dispatch routes inference tasks to remote workers,
  the BackendCatalog absorbs each worker's heartbeat-reported
  catalog into a federated view, and the Scheduler widget aggregates
  slots / load / latency across every registered worker. Today the
  Cluster widget shows worker hardware (CPU, RAM, GPU, NPU) and the
  Scheduler widget shows local-only — the two will merge once Phase
  2 lands.

- **Sequential model loading + idle eviction (Phase 1.5).** Today
  rkllama starts with `--preload qwen3-embedding-0.6b,qwen3-reranker-0.6b,qmd-query-expansion`
  and qmd serve holds those connections open, so the Orange Pi's NPU
  RAM is permanently consumed by chat models even when the user is
  only doing image gen. A user can't fit a bigger image gen model
  alongside the chat models without first manually killing rkllama.
  The fix is on-demand loading at the resource scheduler layer:

  1. **Drop preloading** — no `--preload` on rkllama. Load on first
     request.
  2. **Per-resource model registry** in the scheduler tracks what's
     resident on each accelerator (NPU, GPU, CPU), how recently each
     was used, how much memory each occupies.
  3. **Idle eviction** after a configurable per-model-class TTL.
     Suggested defaults — overridable per model in the catalog
     manifest, and overridable per agent via user pinning:

     | Model class                | Default TTL |
     |----------------------------|-------------|
     | Image gen (SD/Flux/etc.)   | 5 min       |
     | STT (Whisper)              | 2 min       |
     | TTS                        | 2 min       |
     | Embedding                  | 30 sec      |
     | Reranker                   | 15 sec      |
     | Query expansion            | 15 sec      |
     | Chat (small ≤4B)           | 10 min      |
     | Chat (large ≥7B)           | 30 min      |
     | Reasoning                  | 30 min      |

     Rationale: image gen is bursty so 5 minutes captures a typical
     session without holding the 5 GB after the user moves on.
     Embedding / reranker / query-expansion are tiny (~1 s reload)
     and called constantly during ingest then idle for hours, so
     short TTLs are basically free. Large chat models reload slowly
     (~20 s) so a generous 30 min TTL keeps conversation flow snappy.
  4. **LRU eviction under capacity pressure** when a new request needs
     more memory than the resource has free.
  5. **Cluster-aware cache locality** — when routing in cluster mode
     (Phase 2), prefer the worker that already has the model loaded.
  6. **User-pinned always-resident models** for the workloads where
     reload latency would be intolerable (e.g. an embedding model
     hit on every chat turn).

  7. **Lazy lifecycle wrappers for backends that don't support
     lazy load natively.** Some backends (`stable-diffusion.cpp`'s
     `sd-server`, `llama.cpp`'s `llama-server` for the larger
     fallback chat path, `ggml-org/whisper.cpp`'s server) load their
     model at process startup and have no built-in idle eviction.
     Phase 1.5 ships a small Python proxy per backend that:

     - Listens on the user-facing port
     - Starts the underlying server subprocess on the first request
     - Stops the underlying subprocess after the per-class TTL with
       no requests
     - Restarts on the next request, paying the cold-start latency
       once

     The wrapper looks like the `LLMProxy` and `QmdClient` lifecycle
     pattern we already have. ~50 LOC per backend, fully isolated
     so the upstream binaries don't need patching. The CPU/Vulkan
     stable-diffusion.cpp fallback specifically has been the largest
     standing waste on the orange pi (1.6 GB pinned with zero recent
     calls); the wrapper is the right fix for it.

  The qmd upstream maintainer is already on record agreeing this
  belongs in a stacked PR after the centralised-serve PR (#511) lands
  — TAOS implementing this in our qmd-server fork and contributing
  it back is the right path. See the discussion at
  https://github.com/tobi/qmd/pull/511 for the design history.

  This is meaningful enough to deserve its own design pass but small
  enough to land before Phase 2 cluster dispatch. Tracked separately
  as a Phase 1.5 milestone.

- **Core-aware resource model for the NPU scheduler (Phase 1.5
  extension).** The sequential-loading design above models each
  accelerator as a single-dimension budget: memory in, memory out.
  That holds for CUDA and ROCm GPUs where one process owns the whole
  device and memory is the only scarce resource. It does not hold
  for the RK3588 NPU, which has three physical compute cores that
  can host different models in parallel.

  Benchmark data for rkllama (LLM/embedding) on an Orange Pi 5 Plus showed
  that the RK3588 can host multiple concurrent models across its three NPU
  cores. Two independent wins the single-dimension model cannot express:

  1. A solo model using all three cores tensor-parallel runs ~20% faster.
  2. Two sessions on separate cores run with ~1.78x throughput (89% linear
     scaling).

  Neither is expressible if the scheduler only tracks "MB free /
  MB used" on the NPU. Both become trivial if it tracks cores too.

  The extension is additive, not a rewrite of the sequential loading
  design. All of the TTL / LRU / user-pinned behaviour above still
  applies. The scheduler gains one new concept:

  ### Per-backend resource shape

  Each backend adapter declares its own resource shape. The scheduler
  is backend-agnostic and consults the adapter rather than hardcoding
  dimensions.

  | Backend               | Resource shape                      |
  |-----------------------|-------------------------------------|
  | rkllama / rknn (RK3588) | `{ memory_mb, cores: [0, 1, 2] }` |
  | llama.cpp (CUDA)      | `{ vram_mb, gpu_ids: [0, 1] }`      |
  | vllm (CUDA)           | `{ vram_mb, gpu_ids: [0, 1] }`      |
  | llama.cpp (CPU)       | `{ ram_mb }`                        |
  | ollama                | `{ vram_mb }` or `{ ram_mb }`       |

  A resident model records the subset of each resource it holds:

  ```
  LoadedModel(
      model_id,
      backend,
      memory_mb_used,
      resource_holds: dict[str, Any],  # {"cores": [0,1,2]} on NPU
      tp_mode: str,                    # "all" | "0,1" | "0" | ...
      loaded_at, last_used_at,
      priority, pinned,
  )
  ```

  ### Load decision with core pressure

  When a new request arrives for a model that is not resident, the
  scheduler's decision expands from one to two questions:

  1. *Memory pressure?* (unchanged) — evict LRU until there's enough
     headroom, or reject if the request model is too big to fit even
     with everything evicted.
  2. *Resource-hold pressure?* (new) — does the target backend have
     the cores/GPUs/slots this load wants? If yes, pick them and load.
     If no, choose between:
     - **Wait** for a resident model to idle-evict naturally (bounded
       by a configurable `max_wait_ms`, default 500 ms)
     - **Shrink-reload** a lower-priority resident to a smaller mask
       (e.g. SD on `all` → SD on `0,1` to free core 2). Costs one
       reload of the shrinkee. Picked when the shrinkee's reload cost
       is cheaper than the alternative.
     - **Evict-reload** a lower-priority resident entirely (e.g. drop
       an idle STT model to free its core). Same as memory-pressure
       eviction but keyed on resource holds.
     - **Reject** with a 503 if nothing of lower priority is resident
       and waiting would exceed `max_wait_ms`.

  ### Default load policies

  For the RK3588 NPU specifically, the scheduler defaults to:

  | Scenario                      | tp_mode chosen |
  |-------------------------------|----------------|
  | Only model resident           | `all` (3 cores) |
  | Two models resident           | `0,1` + `2`    |
  | Three models resident         | `0` + `1` + `2` |
  | Four or more models           | cannot happen — scheduler evicts down to three first |

  The chosen `tp_mode` is baked into the InferenceSession at load
  time. Changing a resident model's mask means evict + reload —
  there is no runtime mutation path. This makes the decision policy
  important but also keeps the state machine simple.

  ### Backends that don't report cores

  CUDA and CPU backends see no change. Their resource shape stays
  `{ vram_mb }` or `{ ram_mb }`, the scheduler's second-question
  check degenerates to "is there a GPU/core slot free" and returns
  true whenever any device is loadable. The RK3588 NPU is the only
  backend in Phase 1.5 with a non-trivial parallel-core story. We
  revisit this when multi-GPU desktop hosts become a primary
  deployment target (Phase 2+).

  ### Priority hints from the user

  Each agent declares a deployment priority (`always_resident`,
  `interactive`, `background`). The scheduler uses priority to pick
  the victim under pressure:

  - `always_resident` — pinned, never shrunk or evicted. Default
    mask is `all` for solo, or `1-core` if forced to co-resident.
  - `interactive` — default class for user-facing agents. Preferred
    to keep resident but shrinkable when something higher-priority
    wants cores.
  - `background` — ingest, indexing, batch jobs. Shrunk and evicted
    first.

  This extension is RK3588-specific for now. The scheduler interface
  is backend-agnostic, so any future backend that exposes similar
  multi-core topology (e.g. the SambaNova or Intel AI PU backends
  we might add later) plugs in by declaring its own resource shape
  without any scheduler-core changes.

- **Remote backend installation from the controller UI.** The Phase 1
  worker installer (`install-worker.sh`) installs only the worker
  daemon. The worker auto-detects existing backends (Ollama, llama-cpp,
  vLLM, rkllama, etc.) on common ports and reports them — so a fresh
  worker on a box that already runs the user's Ollama install picks
  it up unchanged. What's missing: a "Add backend" flow in the
  Cluster app that lets the user opt into installing a backend on a
  worker that doesn't have one.

  Constraints when this lands:

  1. **Detect first, install only on explicit consent.** No backend
     installs without a user click in the Cluster app.
  2. **TAOS-installed backends live in their own namespace** under
     `~/.local/share/tinyagentos-worker/backends/{backend}/`. Models
     under `~/.local/share/tinyagentos-worker/models/`. Never `/opt`,
     `/usr`, or any system path.
  3. **TAOS-installed backends listen on TAOS-specific ports** that
     don't collide with the user's existing instances of the same
     backend (Ollama default 11434 → TAOS 21434, llama.cpp default
     8080 → TAOS 18080, etc).
  4. **Removing a TAOS-installed backend deletes only the namespaced
     dir**, never user-installed binaries / models / configs.
  5. **Existing user backends are first-class citizens** — the worker
     reports them the same way it reports TAOS-installed ones, and
     the controller routes to them like any other backend.

  This is the safety story for "user installs the worker daemon on
  their existing gaming rig that already has Ollama running" — TAOS
  uses what's there, never re-installs on top, and only adds new
  things in its own corner of the disk.

- **Network model placement (Phase 1.5).** Today, deploying an agent
  that needs a model which isn't present on the chosen worker fails
  at scheduling time. The Models app and deploy wizard will start
  showing models from every node in the cluster (controller + all
  workers + cloud providers) as a single catalog, tagged with the
  host that holds each copy. Once that read-side aggregation is in,
  the write side is: "copy the model from a node that has it to the
  node that needs it, on demand, when the user deploys."

  Transport options, in priority order:

  1. **BitTorrent / peer-to-peer**, as the default. The same
     mirror-policy.md flow TAOS already uses for Rockchip artifacts
     (SHA256-verified) generalises cleanly — every node that has a
     copy of a model seeds it, the destination node joins the swarm
     and pulls from all available peers. This matters when nodes
     are WAN-remote (home server → friend's box, or a small office
     mesh) because a single HTTP pull from one holder bottlenecks
     on that holder's upstream. A swarm scales with the number of
     nodes that already have the model.
  2. **Plain HTTP range-GET** as LAN fallback, for the common case
     where there's exactly one holder and one requester on the same
     subnet. No swarm overhead, no tracker, just `curl -C -` with
     SHA256 verification at the end.
  3. **Out-of-band sneakernet / manual copy** as the "I already have
     this model on a USB drive" escape hatch — the user points the
     worker at a local path and TAOS hashes + registers it.

  Constraints:

  - Every model transfer is SHA256-verified against the source
    node's manifest before the destination marks it available.
    No silent corruption, no "it downloaded but won't load."
  - Transfers are visible in the Cluster app as first-class jobs
    with progress, ETA, source/destination nodes, and a cancel
    button. The user should never wonder "why is my deploy hanging."
  - The worker reports free disk before accepting a transfer; the
    scheduler refuses placement on a node that can't fit the model
    plus headroom for the actual inference workload.
  - No automatic background replication. Transfers happen on
    deploy, explicitly, per user action. We are not building a
    distributed filesystem; we're building on-demand model
    placement.

## Open questions for after approval

- **Bridge service language.** Python for Phase 1. Rust as a candidate
  Phase 3 rewrite if profiling justifies it (memory, startup time, SSE
  fan-out efficiency).
- **The Hermes fork's bridge adapter language** depends on Hermes's
  implementation language. If TypeScript we reuse `taos-bridge-client-ts`;
  if Python we reuse `taos-bridge-client-py`.
- **Plugin MCP server placement** — single Skills gateway per host vs
  one per (gateway, plugin class)? Decided in Phase 3 once we see the
  load profile.

## References

- `docs/design/framework-agnostic-runtime.md` — the load-bearing rule
  this design enforces
- `docs/design/model-torrent-mesh.md` — model distribution layer the
  bridge depends on
- `docs/protocols/taos-bridge-v1.md` (to be written in Phase 1) — the
  bootstrap response schema as a standalone protocol spec
- `docs/runbooks/framework-swap.md` — operational runbook for the swap
  flow
- `docs/runbooks/container-upgrade.md` — operational runbook for the
  container upgrade flow
- `docs/runbooks/add-a-framework.md` (to be written in Phase 1) — the
  contributor guide for catalog framework PRs
