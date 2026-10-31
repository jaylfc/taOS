# taOS Hermes taOSmd memory plugin

**Status:** Proposal, for owner review. Docs only.

## Problem

Hermes is taOS's default agent framework, and taOSmd is taOS's memory. Today
the two barely meet. A Hermes agent deployed with `memory_plugin: taosmd`
gets a block of written instructions about taOSmd in its `AGENTS.md`, and
that is all: nothing recalls memory before a turn, the agent has no tools
to search or remember on purpose, the `memory_search` skill it is offered returns an empty list, and
Hermes keeps its own separate JSON memory whatever `memory_mode` says. The
user sees an agent that "has taOSmd" but does not remember.

Capture is not the gap: taOS already records the agent's conversations and
tool activity into taOSmd (see "Capture already happens" below). The gap is
active use. A real plugin should let Hermes use taOSmd: recall before each
turn, expose search and remember as tools, and give the agent guidance that
matches its `memory_mode`.

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
- **What reaches the container:** `taosmd.agent_rules()` (from the taosmd
  package, takes no arguments today) is written into
  `/root/.hermes/AGENTS.md` between sentinels (`tinyagentos/deployer.py`,
  `tinyagentos/agent_git.py`); the deployer skips that write when
  `memory_mode` is `framework`. The same text is also prepended to the bridge
  system prompt by `assemble_system_prompt` in `tinyagentos/prompt_assembly.py`
  (used by `tinyagentos/bridge_session.py`), which checks only
  `memory_plugin == "taosmd"` and never reads the mode, so a `framework` mode
  agent still gets taOSmd-only rules there. Either way the text speaks as if
  taOSmd is the agent's only memory. The deployer sets `TAOS_MEMORY_MODE` (nothing
  reads it) and `TAOS_USER_MEMORY_URL` when user-memory access is granted.
  No taOSmd URL or memory token is passed in.
- **Agent memory API:** `tinyagentos/routes/memory.py` offers browse, search,
  collections and chunk reads under the `memory_read` scope. A write route
  (`POST /api/memory/remember`, scope `memory_write`) is in review in the
  open memory_remember PR stack, not yet on `dev`.
- **Skill:** `memory_search` is listed for Hermes, but
  `_skill_memory_search` in `tinyagentos/routes/skill_exec.py` is a stub
  that returns no results.
- **Capture already happens:** taOS builds a `taosmd.Archive` at startup
  (`tinyagentos/app.py`). Chat messages are recorded as `conversation` events
  (`tinyagentos/routes/chat.py`, `_archive_message` in
  `tinyagentos/channel_hub/router.py`), the bridge dual-writes tool calls,
  tool results, errors and reasoning (`tinyagentos/bridge_session.py`), and
  `POST /api/archive/record` (`tinyagentos/routes/archive.py`) takes other
  events. `tinyagentos/scheduling/job_worker.py` then splits the archive into
  sessions, catalogs and crystallizes them into the session catalog, crystals
  and knowledge graph.
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
| **tools** | `memory_search(query, limit)` and `memory_remember(text, tags)`, registered as Hermes tools |
| **guidance** | the agent's rules block comes from `agent_rules(mode)` (below), so the agent knows when to search and what is worth remembering |
| **user memory** | when `TAOS_USER_MEMORY_URL` is set, also search the user namespace (read only) |

There is deliberately no after-turn write. Exchanges and tool activity already
reach taOSmd through the archive capture listed under "Current state", and the
catalog and crystallize jobs turn them into durable memory. A plugin write
would be a second capture path for the same turns (write fan-out, duplicate
sessions). `memory_remember` is different: it is an agent-initiated tool call
for a fact the agent chooses to keep, not automatic capture.

### memory_mode

| Mode | Hermes own memory | taOSmd plugin | Rules text (`agent_rules(mode)`) |
|---|---|---|---|
| `both` (default) | on | recall, tools | complementary: two memories, each with a job |
| `taosmd` | disabled in `config.yaml` | recall, tools | taOSmd is the agent's memory |
| `framework` | on | not installed | the framework's own memory; no taOSmd instructions |

`install_hermes.sh` reads `TAOS_MEMORY_MODE` and writes the matching
`config.yaml`, so the variable the deployer already sets finally does
something. Changing the mode via `PATCH /api/agents/{slug}/memory` triggers a
config rewrite and a Hermes restart in the container.

### Per-mode rules: `agent_rules(mode)`

The onboarding guide and the injected rules block are one artefact, written
once in the taosmd package and varied by mode. `taosmd.agent_rules()` gains a
`mode` argument (`both`, `framework`, `taosmd`); it is produced on the
controller side, not inside the plugin, so it still reaches a `framework`
mode agent that has no plugin. Both call sites pass the agent's mode: the
AGENTS.md splice in `tinyagentos/deployer.py` and `assemble_system_prompt` in
`tinyagentos/prompt_assembly.py`, which today ignores the mode.

- **`both`:** the agent has two memories. Its own Hermes memory holds
  in-session working context; taOSmd holds durable facts and past sessions.
  Search taOSmd (`memory_search`) before answering about earlier work or the
  user, use `memory_remember` for facts worth keeping beyond this session,
  and follow the precedence rule below on conflict.
- **`taosmd`:** taOSmd is the agent's memory. Same search and remember
  guidance, without the framework memory paragraph.
- **`framework`:** describes the framework's own memory only. It does not
  tell the agent to use taOSmd as its memory and names no taOSmd tools. It
  may say that taOS keeps a conversation archive the user can browse.

Open for the owner: whether `framework` mode writes this text into AGENTS.md
(changing the current skip in the deployer) or keeps the skip and relies on
the prompt-assembly path alone.

### Precedence between framework memory and taOSmd

taOSmd is authoritative for durable facts. Framework memory wins for
in-session working context (the current task, scratch state, what was just
said). When the two disagree on a durable fact, the agent prefers taOSmd and
notes the conflict in its reply rather than silently picking one. This rule
is part of the `both` mode rules text and also governs the one-off import in
open question 5.

### Auth and wiring

- The deployer passes `TAOS_MEMORY_URL` (the controller's memory routes,
  reached the way the bridge already reaches the controller) and uses the
  agent's existing registry identity: the plugin calls with the agent's
  scoped token, `memory_read` for search and `memory_write` for
  `memory_remember`. No new credential type.
- The memory routes already fence an agent to its own namespace; the plugin
  never names another agent.
- Failure is soft: if taOSmd is unreachable the turn proceeds without recall,
  `memory_remember` calls queue in a small on-disk outbox in the container
  and retry, and the agent's status shows "memory degraded".

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
2. **Recall and guidance:** the plugin's before-turn search and injection
   (or the bridge fallback), and `agent_rules(mode)` wired into the deployer
   and prompt assembly. Tests with a stub memory server and per-mode rules
   text; live check on one Hermes agent.
3. **Tools:** `memory_search` and `memory_remember`, the latter after the
   `memory_write` route lands on `dev`.
4. **Real `memory_search` skill** in `skill_exec.py`.
5. **Mode switching at runtime** with config rewrite and restart.
6. **Same plugin pattern for openclaw** (separate design note).

## Open questions

1. Does the pinned Hermes version expose a memory-provider hook, or should
   taOS upstream one? Phase 1 checks and records the answer.
2. Default `memory_mode` for new Hermes agents: the default stays `both`,
   per the standing decision that memory is complementary by default
   (`tinyagentos/config.py`, `tinyagentos/deployer.py`,
   `tinyagentos/routes/agents.py`). `taosmd` remains an opt-in mode.
3. Should recall cite memory ids in the reply so the user can open them in
   the Memory app?
4. Token budget for injected memory on small local models (Pi class): fixed
   default or derived from the model's context window? Proposal: derived,
   capped at 15 percent.
5. Should Hermes' existing JSON memory be imported into taOSmd once when a
   user switches an agent to `taosmd` mode? If so, the precedence rule
   applies: taOSmd is authoritative for durable facts, so an imported entry
   that conflicts with an existing taOSmd fact does not overwrite it; the
   conflict is recorded for the user to resolve.
