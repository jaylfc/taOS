# Framework-Agnostic Runtime

**Status:** Active — load-bearing architectural rule. All container, agent, and
service work must honour it.

The rule that makes taOS a platform instead of a framework:

> **Containers hold their own state. Hosts hold the federation.**

The host holds the LLM proxy, the trace API, chat and federation services,
and the storage pool. Collaboration data shared between agents lives in
host-side collaboration services (Forgejo, Garage) the agents explicitly
opt into. Everything inside one agent's world — its apt packages, framework
binaries, openclaw.json, recycle bin, agent-authored files — lives inside
its container rootfs and travels with it as a snapshot archive.

See `docs/design/architecture-pivot-v2.md` for the full decision record that
produced this thesis.

## The rule, stated precisely

An agent container is allowed to contain:

- The base OS (Debian bookworm, Alpine, whatever)
- The agent framework (LangChain, Autogen, CrewAI, a bespoke loop, anything)
- Framework runtime dependencies (Python venv, node_modules, compiled binaries)
- Read-only configuration written at install time (ports, endpoints, the
  agent's own identity — written by `openclaw install.sh` inside the container)
- Per-agent memory (chat history, embeddings, vector stores, retrieved facts)
- The agent's workspace and any files the agent has produced
- Framework config, shell dotfiles, caches, and cloned code under `/root`
- Tool state (browser profiles, shell history, MCP server state)

An agent container must **not** contain:

- Secrets or API credentials (fetched via API on demand from host secrets store)
- Cached embedding weights or models owned by the host embedding service
- SQLite databases that are shared across multiple agents
- The local auth token (machine-bound; must not leave the host)
- State that belongs to a host-level federation service (user memory,
  agent-to-agent messages, LiteLLM config)

The test remains: if you take an incus snapshot of the container and restore
it on a fresh machine, the agent comes back identically with zero user-visible
state loss. If anything the agent owns is missing after that restore, the rule
is being violated.

## Why the pivot

The original thesis — "containers hold code, hosts hold state" — was written
to make framework swaps and container upgrades cheap. Those goals remain valid
and are still achieved. The pressure to move agent state inside the container
came from a different direction: **operational complexity at archive time**.

The old `_archive_agent_fully` was a six-step distributed transaction:
stop container, rename container, move three host directory trees (workspace,
memory, home), revoke LLM key, export chat, update config. Partial failure
between any step left the container renamed but directories not yet moved, or
vice versa. The runbook documented those failure modes explicitly because
they were real.

Moving state inside the container rootfs makes the archive operation one incus
command: `incus snapshot create taos-agent-{slug} taos-archive-<ts>`. The
snapshot captures the container plus all its state atomically. On a btrfs pool
(taOS's chosen backend) this is copy-on-write and near-instantaneous.

Four direct consequences:

1. **Archive is atomic.** A single snapshot either succeeds completely or
   leaves the live container untouched. No half-archived state.

2. **apt packages survive archive/restore.** Framework binaries and OS
   packages installed by the agent are inside the container rootfs, so they
   are captured by the snapshot. Restore brings the agent back exactly.

3. **Framework-agnostic semantics intact.** The host still does not hold
   agent state in a form bound to a specific framework. The container image
   is the portable unit. Swapping the framework means rebuilding the
   container from a different image — the same as before.

4. **Single failure domain per agent.** All of an agent's mutable state is
   in one place. Backup, migrate, and restore all operate on that one unit.

## Current state vs. the rule

Audit as of **2026-04-17**. Pass = aligned with rule. Fail = needs migration.

| Concern | Where it lives | Verdict |
|---|---|---|
| LLM chat routing | LiteLLM proxy on host, containers call via injected `OPENAI_BASE_URL` | **Pass** |
| Skills / MCP tools | Skill MCP server on host, containers call via injected `TAOS_SKILLS_URL` | **Pass** |
| User memory | SQLite on host (`data/user_memory.db`), containers call via `TAOS_USER_MEMORY_URL` | **Pass** |
| Agent-to-agent messages | SQLite on host (`data/agent_messages.db`) | **Pass** |
| Secrets | SQLite on host (`data/secrets.db`), agents fetch via API on demand | **Pass** |
| Workspace files | Inside container rootfs (`/workspace`) — captured by snapshots | **Pass** |
| Agent memory dir | Inside container rootfs (`/memory`) — captured by snapshots | **Pass** |
| openclaw.json + env file | Written by `install.sh` inside the container at install time; lives at `/root/.openclaw/` | **Pass** |
| QMD embedding + index service | Single host `qmd.service` systemd unit on :7832 routing per-tenant via `dbPath` | **Pass** |
| Per-agent memory isolation | `data/agent-memory/{name}/index.sqlite` inside container; addressed by dbPath | **Pass** |
| LiteLLM `/v1/embeddings` | Auto-discovers ollama-compatible backends, exposes `taos-embedding-default` alias | **Pass** |
| Container upgrade / framework swap | Runbooks in `docs/runbooks/`, automated test pending | **Gap** |

## Migration — what changed vs. the old bind-mount model

### Three bind mounts removed

The deployer previously attached three host-side directories into every agent
container:

- `{data_dir}/agent-workspaces/{slug}/` → `/workspace`
- `{data_dir}/agent-memory/{slug}/` → `/memory`
- `{data_dir}/agent-home/{slug}/` → `/root`

As of Phase 2.A (`refactor(deployer): snapshot-model`), all three are removed.
Agent state now lives entirely inside the container rootfs. There is no host
path to move, rename, or rsync at archive or restore time.

### openclaw bootstrap moved inside the container

The deployer previously wrote `/root/.openclaw/openclaw.json` and
`/root/.openclaw/env` onto the host path `agent-home/{slug}/.openclaw/` before
starting the container. As of Phase 2.A, `openclaw install.sh` writes both
files from inside the container at install time, using env vars injected by the
deployer (`TAOS_AGENT_NAME`, `TAOS_MODEL`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`,
`TAOS_BRIDGE_URL`, `TAOS_LOCAL_TOKEN`). Both files live in the container rootfs
and travel with snapshot archives.

### agent_env.py removed

`tinyagentos/agent_env.py` (the `update_agent_env_file` helper) is deleted as
of Phase 2.C. Env rewrites now go through `incus config set environment.<KEY>=<value>`,
exposed as `containers.set_env(container_name, key, value)`. At restore time
this is used to inject the freshly minted LiteLLM key, followed by
`incus exec <container> systemctl restart openclaw` to pick it up.

## Per-agent trace capture

Every agent's trace events land in a dedicated host directory that is
bind-mounted into the container at `/root/.taos/trace/`. This is the **only**
host bind mount remaining in the Phase 2 snapshot model. Separating the trace
store from the container rootfs means traces accumulate on the host regardless
of container lifecycle and are accessible to the host API without entering the
container.

**Path layout.**

```
{data_dir}/trace/{slug}/
    YYYY-MM-DDTHH.db        primary: one aiosqlite DB per UTC hour
    YYYY-MM-DDTHH.jsonl     fallback: appended only when the DB write fails
    YYYY-MM-DDTHH.late.jsonl late-arrival sidecar for sealed buckets
```

**Hourly buckets.** One file per UTC hour bounds individual file size and
matches the librarian's natural query scope — a single summarisation pass
rarely needs more than a few hours of history. Bucket routing uses the
event's `created_at`, not wall-clock at write time, so a 14:59:59.999 event
flushed at 15:00:00.001 still lands in the T14 file; rollover never drops
events. The registry keeps the current and previous hour open and closes
older connections opportunistically.

**Why separate from the container.** The trace directory is a dedicated
bind-mount, not part of the container rootfs. Traces accumulate on the host
through snapshot replacements and are always reachable by the host API at
`{data_dir}/trace/{slug}/` without entering the container. Pre-archive trace
history is preserved even after the container snapshot is purged.

**Envelope v1 fields.**

```
v, id, trace_id, parent_id, created_at, agent_name,
kind, channel_id, thread_id, backend_name, model,
duration_ms, tokens_in, tokens_out, cost_usd, error, payload
```

Valid kinds: `llm_call`, `message_in`, `message_out`, `tool_call`,
`tool_result`, `reasoning`, `error`, `lifecycle`.

`SCHEMA_VERSION` is exported from `tinyagentos/trace_store.py`; bump it and
provide a migration if any field name changes.

**Zero-loss contract.** The primary write path is `INSERT OR IGNORE` into
SQLite (idempotent on `id`). On any SQLite exception the envelope is
appended to the sibling `.jsonl` fallback. If even the JSONL write fails
the event is logged at ERROR level. The `list()` method merges `.db` rows
and `.jsonl` lines before returning, so neither path is invisible to
readers. See `tinyagentos/trace_store.py::AgentTraceStore.record`.

**Librarian consumption.** taOSmd reads traces newest-first via
`GET /api/agents/{name}/trace` or direct SQL. The librarian summarises and
may annotate but does not delete raw envelopes. See
`docs/design/user-memory.md` for how per-agent traces relate to user memory.

## Agent archive / restore

`DELETE /api/agents/{name}` archives rather than hard-deletes an agent. The
distinction matters: a hard delete is irreversible; an archive preserves
everything and allows restore with minimal friction.

**Why archive instead of delete.** Chat history, trace data, workspace
files, and trained-context embeddings represent real user investment. A
misbehaving agent should be paused or archived, not erased. Archive also
makes "clone by archive → restore as different slug" possible without a
dedicated clone endpoint.

**What the archive step does** (source: `tinyagentos/routes/agents.py::_archive_agent_fully`):

1. Force-stops the container.
2. Takes a named incus snapshot: `incus snapshot create taos-agent-{slug} taos-archive-<ts>`.
3. Exports chat history to `{data_dir}/archive/{slug}-<ts>/chat/chat-export.jsonl`
   (host-owned; preserved even if the snapshot is later purged).
4. Revokes the agent's LiteLLM key.
5. Flags the agent's DM channel archived in the chat store.
6. Moves the config entry from `config.agents` to `config.archived_agents`,
   recording the `snapshot_name`.

**What stays with the archive.** Trace data lives in `{data_dir}/trace/{slug}/`
on the host and is NOT included in the snapshot — it remains accessible by the
host API for forensics after the agent is archived. Pre-archive trace history
is fully preserved.

**Restore path** (`POST /api/agents/archived/{id}/restore`). Slug collision is
handled by appending a numeric suffix (`foo` → `foo-2`). The snapshot is
restored with `incus snapshot restore`. A new LiteLLM key is minted and written
into the container via `containers.set_env`. The openclaw service inside the
container is restarted to pick up the new key.

**Purge** (`DELETE /api/agents/archived/{id}`). Calls `incus delete --force`
on the container (which also destroys all its snapshots). Wipes the
`archive/{slug}-<ts>/` directory. Irreversible. Trace history remains on the
host in `{data_dir}/trace/{slug}/` until explicitly removed.

## Programmatic access (local token)

Scripts, the LiteLLM callback, and in-container agent runtimes authenticate
to the taOS API using a persistent local token rather than browser sessions.

**Token file.** `{data_dir}/.auth_local_token` — generated on first call to
`AuthManager.get_local_token()` (see `tinyagentos/auth.py`), written with
0600 permissions so only the process owner can read it. Never rotated
automatically; delete the file to force regeneration.

**Middleware.** `auth_middleware` accepts `Authorization: Bearer <token>` in
addition to session cookies. The local token grants the same access level as
a logged-in admin session; it is intended only for same-host callers.

**Consumers.**

- The LiteLLM callback (`tinyagentos/litellm_callback.py`) runs in-process
  with the LiteLLM proxy subprocess. It probes `/data/.auth_local_token` and
  `~/.taos/.auth_local_token` in order, then falls back to the
  `TAOS_LOCAL_TOKEN` env var injected by the deployer.
- In-container agent runtimes receive `TAOS_LOCAL_TOKEN` as an env var (set
  at deploy time from the token file) and post traces to `TAOS_TRACE_URL`.
- A future taOS CLI will read the token file directly.

**Scope.** The token file is bound to the host machine. It must not leave
the machine — never commit it, never copy it to workers, never include it in
backups that leave the network boundary.

## Rule application checklist (for future changes)

When adding a new feature that touches an agent container, answer these
before merging:

1. Does this state belong to a single agent (lives inside the container) or
   does it need to be shared across agents or the wider federation (lives on
   the host in a collaboration service)?
2. How is this state reached from inside the container — container-local path,
   injected env var pointing at a host service, or host API callback?
3. If the container snapshot is restored on a fresh machine, does the feature
   come back identically without manual intervention?
4. If the user swaps the framework, does the feature come back identically?
5. Is there a test that proves #3 and #4, or is that being added alongside
   this change?
6. If this agent is archived, is the archive a single portable unit (incus
   snapshot) or does it require coordinated multi-step moves? If the latter,
   re-examine whether the state can live inside the container.

A "no" on any of these is a conversation, not necessarily a block — but it
needs to be surfaced in the PR, not discovered a year later when the upgrade
path breaks.

## Updating the qmd fork

taOS runs a fork of [tobilg/qmd](https://github.com/tobilg/qmd) at
[jaylfc/qmd](https://github.com/jaylfc/qmd) (branch `feat/remote-llm-provider`).
The fork adds multi-tenant serve mode, per-tenant `dbPath` routing, and
ingest/delete-chunk endpoints.  These changes are not yet upstream.

### When to rebase

Rebase onto upstream when:
- Upstream merges the outstanding PR (currently [#511](https://github.com/tobilg/qmd/pull/511))
- A security fix or critical bug is released upstream
- A new upstream feature is needed by taOS

### How to rebase

1. **Fetch upstream.**
   ```bash
   git remote add upstream https://github.com/tobilg/qmd.git
   git fetch upstream main
   ```

2. **Rebase the fork onto upstream.**
   ```bash
   git checkout feat/remote-llm-provider
   git rebase upstream/main
   ```
   Resolve conflicts.  taOS-specific changes are concentrated in:
   `src/cli/serve.ts`, `src/cli/qmd.ts`, `src/cli/ingest.ts`,
   and `package.json` (bin entries).

3. **Test locally.**
   ```bash
   npm install
   npm run build
   npm test
   # Smoke-test serve mode with per-tenant routing
   node dist/cli/qmd.js serve --port 7833 --dbPath /tmp/test-qmd/index.sqlite
   ```

4. **Publish a new npm version.**
   ```bash
   npm version patch   # or minor, per semver
   npm publish
   ```

5. **Update the pinned version in taOS.**
   - Bump the version in `scripts/install-server.sh` (search for `@jaylfc/qmd@`)
   - Update `docs/agent-qmd-serve-setup.md` with the new version
   - Commit: `chore: bump qmd pin to <new-version>`

### When upstream PR #511 merges

Once tobig/qmd#511 lands, the fork should collapse back to thin PRs:
rebase onto the new upstream main, drop any changes that were merged,
and re-publish.  The goal is to eventually eliminate the fork.

## Related

- `docs/design/architecture-pivot-v2.md` — full decision record for the
  container-holds-state pivot; sections 1–3 cover the old model's costs and
  the reasoning behind whole-container snapshots
- `docs/design/model-torrent-mesh.md` — model weights distribution (host-side
  concern; containers don't hold weights either)
- `docs/design/cluster-dispatch.md` — migrating agents across workers
- `docs/design/user-memory.md` — user's own long-lived notes/context; cross-
  references the per-agent trace layer
- `docs/runbooks/agent-archive-restore.md` — step-by-step archive, restore,
  and purge procedures for the snapshot model
- `docs/runbooks/trace-querying.md` — using the trace API for forensics and
  cost attribution
- `docs/superpowers/specs/2026-04-11-taos-framework-integration-bridge-design.md`
  — TAOS Framework Integration Bridge: the concrete design for routing an
  OpenClaw agent through Hermes and back, enabled by this rule
- Issues #29, #30, #32, #33, #34 — backend-driven scheduler wiring

## Host firewall: incus through docker's DROP

When Docker and incus are both installed on the same host, Docker sets the
kernel's `FORWARD` policy to `DROP` and inserts a `DOCKER-USER` jump at the
top of the `FILTER FORWARD` chain. Docker then populates its own `DOCKER`
chain with `ACCEPT` rules — but only for bridges it manages. Incus-created
bridges (`incusbr0` by default) never appear in those rules, so all forwarded
traffic from taOS agent containers falls through to the DROP policy. The
symptom is selective connectivity loss inside containers: domains routed via
Cloudflare's CDN (with short-TTL cached paths) may still appear reachable
while direct TCP to others (e.g. github.com) times out.

The Docker-documented fix is to insert `ACCEPT` rules into `DOCKER-USER`
for the bridges that Docker doesn't manage.

`scripts/host-firewall-up.sh` does this idempotently at boot: it checks
whether `DOCKER-USER` exists (no-op if Docker isn't installed), then inserts
`-i incusbr0 -j ACCEPT` and `-o incusbr0 -j ACCEPT` guards before the DROP
fall-through, skipping each insertion if it's already present.
`scripts/host-firewall-down.sh` reverses this on service stop.

`systemd/tinyagentos-host-firewall.service` is a `Type=oneshot RemainAfterExit`
unit ordered `After=docker.service incus.service` and `Before=tinyagentos.service`,
so containers always have working networking before the first agent is started.
`install.sh` drops the scripts into `/opt/tinyagentos/scripts/` and enables
the unit. Set `BRIDGES` in the unit's environment to cover additional bridges
beyond `incusbr0`.

See `docs/design/lxc-docker-coexistence.md` for the full policy, install-scenario coverage, and operational runbook.

## Related code

- `tinyagentos/trace_store.py` — `AgentTraceStore` + `TraceStoreRegistry`
- `tinyagentos/routes/trace.py` — `POST /api/trace`, `GET /api/agents/{name}/trace`,
  `POST /api/lifecycle/notify`
- `tinyagentos/litellm_callback.py` — `TaosLiteLLMCallback` posts `llm_call`
  traces automatically on every LiteLLM completion
- `tinyagentos/containers/__init__.py` — `set_env`, `snapshot_create`,
  `snapshot_restore`, `snapshot_list`; `add_proxy_device` attaches incus
  proxy devices so the container reaches host services via 127.0.0.1
