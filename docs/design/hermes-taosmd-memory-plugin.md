# taOS Hermes taOSmd memory plugin

**Status:** Proposal, for owner review. Docs only.

## Problem

Hermes is taOS's default agent framework, and taOSmd is taOS's memory. Today
the two barely meet. A Hermes agent deployed with `memory_plugin: taosmd`
gets a block of written instructions about taOSmd in its `AGENTS.md`, and
that is all: nothing recalls memory before a turn, nothing writes the turn
back, the `memory_search` skill it is offered returns an empty list, and
Hermes keeps its own separate JSON memory whatever `memory_mode` says. The
user sees an agent that "has taOSmd" but does not remember.

A real plugin should make taOSmd Hermes' memory: recall before each turn,
write back after it, expose search and write as tools, and honour the
agent's `memory_mode`.

## Current state

- **Hermes in taOS:** `tinyagentos/frameworks.py` makes Hermes the default
  framework. The deployer runs `tinyagentos/scripts/install_hermes.sh`,
  which installs upstream hermes-agent, points `/root/.hermes/config.yaml` at
  the LiteLLM proxy, and writes the taOS bridge
  (`/opt/taos/taos-hermes-bridge.py`). The bridge listens on the taOS chat
  SSE stream and forwards each user message to Hermes'
  `/v1/chat/completions` with a fixed system prompt.
- **Catalog entry:** `app-catalog/agents/hermes/framework-integration.yaml`
  still declares `memory: none` ("Hermes uses its own memory system"), and
  `app-catalog/agents/hermes/manifest.yaml` pins an old pip version. Both are
  stale against the real install script.
- **Agent memory settings:** `memory_plugin` (taosmd | none), `memory_mode`
  (both | framework | taosmd) and `memory_config`, validated in
  `tinyagentos/routes/agents.py`; deploy registers the agent with taOSmd.
- **What reaches the container:** `taosmd.agent_rules()` is written into
  `/root/.hermes/AGENTS.md` between sentinels (`tinyagentos/deployer.py`,
  `tinyagentos/agent_git.py`). The deployer sets `TAOS_MEMORY_MODE` (nothing
  reads it) and `TAOS_USER_MEMORY_URL` when user-memory access is granted.
  No taOSmd URL or memory token is passed in.
- **Agent memory API:** `tinyagentos/routes/memory.py` offers browse, search,
  collections and chunk reads under the `memory_read` scope. A write route
  (`POST /api/memory/remember`, scope `memory_write`) is in review in the
  open memory_remember PR stack, not yet on `dev`.
- **Skill:** `memory_search` is listed for Hermes, but
  `_skill_memory_search` in `tinyagentos/routes/skill_exec.py` is a stub
  that returns no results.
- **Elsewhere:** `tinyagentos/routes/memory_management.py` manages taOSmd
  backends and per-agent memory config; `tinyagentos/scheduling/job_worker.py`
  catalogs and crystallizes sessions; `docs/design/user-memory.md` covers the
  shared user memory.

## Design

### Shape: a Hermes plugin, not more bridge code

The plugin lives in this repo under `tinyagentos/agent_plugins/hermes_taosmd/`
and is installed into the container by `install_hermes.sh` as a Hermes memory
provider (Hermes loads memory backends from its config). Putting recall in the
bridge instead would only cover chat messages that arrive through taOS,
missing Hermes' own scheduled and tool-driven turns, and would fork the
bridge's prompt logic. If the upstream Hermes version taOS pins has no
memory-provider hook, phase 1 falls back to the bridge and the open question
below decides whether to upstream a hook.

### What the plugin does

| Hook | Behaviour |
|---|---|
| **before turn** | `search` taOSmd for the user message (top k, default 6, token budget from `memory_config`) and inject results as a "Relevant memory" system block with source ids |
| **after turn** | write the exchange to taOSmd as a session event; the existing catalog and crystallize jobs turn sessions into durable memory, so the plugin does not summarise |
| **tools** | `memory_search(query, limit)` and `memory_remember(text, tags)`, registered as Hermes tools |
| **user memory** | when `TAOS_USER_MEMORY_URL` is set, also search the user namespace (read only) |

### memory_mode

| Mode | Hermes own memory | taOSmd plugin |
|---|---|---|
| `taosmd` | disabled in `config.yaml` | recall, write, tools |
| `both` | on | recall, write, tools |
| `framework` | on | not installed |

`install_hermes.sh` reads `TAOS_MEMORY_MODE` and writes the matching
`config.yaml`, so the variable the deployer already sets finally does
something. Changing the mode via `PATCH /api/agents/{slug}/memory` triggers a
config rewrite and a Hermes restart in the container.

### Auth and wiring

- The deployer passes `TAOS_MEMORY_URL` (the controller's memory routes,
  reached the way the bridge already reaches the controller) and uses the
  agent's existing registry identity: the plugin calls with the agent's
  scoped token, `memory_read` for search and `memory_write` for remember and
  session writes. No new credential type.
- The memory routes already fence an agent to its own namespace; the plugin
  never names another agent.
- Failure is soft: if taOSmd is unreachable the turn proceeds without recall,
  writes queue in a small on-disk outbox in the container and retry, and the
  agent's status shows "memory degraded".

### Fixing the stubs

- `_skill_memory_search` stops being a stub and calls the same search the
  plugin uses, so frameworks without a plugin also get real results.
- `framework-integration.yaml` and the Hermes manifest are brought in line
  with the real install (memory: taosmd plugin, upstream version).

## Phased plan

1. **Plumbing:** pass `TAOS_MEMORY_URL` and the scoped token into the
   container; make `install_hermes.sh` honour `TAOS_MEMORY_MODE`; fix the
   stale catalog entry. Tests: deployer env and generated `config.yaml` per
   mode.
2. **Recall:** the plugin's before-turn search and injection (or the bridge
   fallback). Tests with a stub memory server; live check on one Hermes
   agent.
3. **Write-back:** session writes and `memory_remember`, after the
   `memory_write` route lands on `dev`.
4. **Real `memory_search` skill** in `skill_exec.py`.
5. **Mode switching at runtime** with config rewrite and restart.
6. **Same plugin pattern for openclaw** (separate design note).

## Open questions

1. Does the pinned Hermes version expose a memory-provider hook, or should
   taOS upstream one? Phase 1 checks and records the answer.
2. Default `memory_mode` for new Hermes agents: `both` (safe, duplicates
   memory) or `taosmd` (one source of truth)? Proposal: `taosmd` once phase 3
   lands.
3. Should recall cite memory ids in the reply so the user can open them in
   the Memory app?
4. Token budget for injected memory on small local models (Pi class): fixed
   default or derived from the model's context window? Proposal: derived,
   capped at 15 percent.
5. Should Hermes' existing JSON memory be imported into taOSmd once when a
   user switches an agent to `taosmd` mode?
