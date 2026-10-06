# Design: recipe-driven routing for llm_gateway

**Status:** Direction approved (jaylfc, PR #3565 comment 6024660533). Revision 2 folds in the
answers to the seven open questions and the lead-review findings (section 12). Next: a lab
prototype, then code PRs.
**Date:** 2026-10-06
**Supersedes:** nothing in code. `docs/design/plan-llm-proxy.md` (the LiteLLM-era plan) is
historical; there is no G2 router design doc, so this is the first one.

## 1. Context: what the gateway does today

Read from `tinyagentos/llm_gateway/` on `dev`:

- `router.chat_completions` validates the body, checks `GatewayCaller.may_use`, expands
  `taos-default` through `resolve.default_chat_model`, then takes
  `find_routes(routing_table(state), name)`. The table is `litellm_config.build_model_list`
  over `config.backends`, rebuilt per request. `resolve.resolve()` exists but the route inlines
  the same steps.
- Dispatch is by `Route.provider`: `anthropic` goes to `anthropic.chat_completion_anthropic` /
  `chat_completion_stream_anthropic`; OpenAI-compatible and Ollama-shaped routes go to
  `forward.chat_completion` / `chat_completion_stream`.
- **Failover exists, but only within one model name.** `forward._call_with_retry` and
  `_stream_with_retry` try every route that shares the requested `model_name`, in priority
  order, on 5xx, timeout or unreachable. A failed backend sits in a 60 s cooldown
  (`_put_in_cooldown`), a stream is retried only before its first byte, and each move records a
  `model.route` event (`_record_route_change`, reason `failover`).
- **What does not exist:** routing across model names, escalation, de-escalation, or any
  coordination of which model is resident on a GPU.
- `stt.py` and `tts.py` are explicitly "no fallback of any kind". `embeddings.py` reuses the chat
  failover. `listener.create_agent_listener_app` (host port 7838) is a path allowlist.

Building blocks outside the package:

- `tinyagentos/lifecycle_manager.py` `LifecycleManager`, wired as `app.state.lifecycle_manager`
  in `app.py` and used by `routes/providers.py` (`/api/providers/{name}/start|stop`):
  - `start(name)` runs the backend's `start_cmd` with `asyncio.create_subprocess_shell`, waits
    for that command to **exit**, then polls `<url>/health` until `_probe_health` sees JSON
    `status` in `ok|healthy|running`, within `startup_timeout_seconds`;
  - `drain_and_stop(name, force)` sets `draining`, waits in `_wait_for_drain` up to
    `_DRAIN_TIMEOUT_SECONDS` (60), then runs `stop_cmd`. `_wait_for_drain` reads
    `catalog.in_flight_count`, which `BackendCatalog` does not define, so **today the drain
    returns at once**;
  - `notify_task_complete(name)` (called by `forward._notify_lifecycle`) restarts a keep-alive
    timer; `keep_alive_minutes: 0` means never auto-stop.
  - Lifecycle states live in `BackendCatalog._lifecycle_states`; the catalog keeps its own copy
    of the backend list (`self._backends_config = list(backends)`).
- `tinyagentos/lazy_backend_proxy.py` `LazyBackendProxy` (lazy TCP proxy, idle stop) has no
  production caller. Per the Q1 decision it is **not** used here.
- `tinyagentos/gpu_lease.py`: the pure half of the A2A GPU lease (#893): `open_claims` (fold
  keyed by node and identity, a re-CLAIM replaces) and `evaluate_admission` (another holder
  blocks outright; `replace_own` for an idempotent re-claim). `routes/a2a_gpu_lease.py` is the
  HTTP half: bus line plus cluster lease (TTL 300 s default, 3600 s max), VRAM from
  `system_stats.read_nvidia_vram`, fail closed (503) when the bus channel cannot be read, and
  agent callers authenticated by `check_agent_scope` (`a2a_send` / `a2a_receive`).

The motivating stack is in the omp-strata lab (experiments 015, 017, 019): on one 12 GB RTX
3060, Bonsai 2 27B is the fast default (8/8 on the lab bench at 4.56 min per pass, about 42
tok/s decode at depth) and Strata (Qwen3.8-Flash-Next Coder) is the stronger, slower model with
a 262K window and hot-loaded vision. Only one fits at a time.

## 2. Goals and non-goals

Goals:

1. A declarative **recipe** that describes a validated model stack for one hardware class and
   that the gateway can execute: which backends exist, which role each serves, when to switch.
2. Recipe-driven resolve: a client asks for a role alias; the router picks the backend per
   request, with per-conversation state so a conversation escalates and returns cleanly.
3. Safe swaps on a single GPU through `LifecycleManager` and the GPU lease, with rollback and a
   defined answer for clients that arrive mid-swap.
4. An optional per-model, per-harness **repair stage** (callshim-style) that runs only
   A/B-validated transforms.
5. **Swaps are a privilege.** No caller can cause a GPU swap unless its own grants allow the
   escalation role, and every caller's swap rate is bounded.

Non-goals:

- **No behaviour change without an active recipe.** Every code path above runs exactly as
  today, byte for byte. This is a tested invariant (section 10).
- stt and tts stay no-fallback; recipes never touch `stt.py`, `tts.py` or their routes.
- The Anthropic translator, embeddings routing and the 7838 listener allowlist are unchanged.
- Replanning is the harness's job (Q4): the router never injects messages into a conversation.
- No multi-GPU placement, no cross-node scheduling, no recipe editing from the UI or API.

## 3. Recipe format

YAML. **Built-ins ship as package data** in `tinyagentos/llm_gateway/recipes/builtin/`
(`<id>.yaml` plus an optional `<id>/` directory for companion files), listed under
`[tool.setuptools.package-data]` in `pyproject.toml` the way `tinyagentos.llm_usage` ships its
price table. Operator recipes go in `<data_dir>/recipes/` (gitignored, like `data/config.yaml`).
The file stem is the recipe id; an operator recipe with a built-in's id is refused, not merged.

| Key | Meaning |
|---|---|
| `id`, `version`, `summary` | Identity. `version` is an integer bumped on any change. |
| `hardware` | `vram_mib`, `ram_gib`, `gpu`, `single_gpu: true`. Activation refuses a recipe whose `vram_mib` exceeds the probed card. |
| `backends` | Named engines: `engine`, `build` (repo, ref), `models` (files with `sha256` and a source URL at a pinned revision), `port`, `health`, `capabilities` (`text`, `vision`), `launch` (`argv` list and `env`), `memory` (`vram_mib` claimed when resident), `load_timeout_s`, `stop_grace_s`. |
| `roles` | `default`, `escalation`, `vision`, `side`: a backend (or an existing taOS model name via `model_ref`) and the upstream model id. |
| `aliases` | Model names clients send, mapped to roles. |
| `routing` | Triggers, de-escalation, dwell, swap limits and rate limits. |
| `repairs` | Per backend and harness: transforms with `status` and `evidence`. Only `validated` ones run. |
| `harness_hints` | Sampling, thinking effort and budget, max tokens per harness. Advisory. |
| `results` | Measured numbers with links to the lab evidence. |

Path rules: model files are relative to `<data_dir>/models/`; `{recipe_dir}` expands to the
recipe's own companion directory (package data for a built-in); engine binaries must sit under
an approved engines root (`/opt/taos/engines` by default). Anything else is refused. Every model
file needs a sha256; `launch.argv` is a list, never a shell string.

### 3.1 Example: `tinyagentos/llm_gateway/recipes/builtin/12gb-bonsai-strata.yaml`

Bonsai values are the lab's (017 `fetch.sh`, `serve-bonsai.sh`). Strata file hashes are not
recorded in the lab yet and are marked `TODO`, so the loader refuses this file until they are
filled. Escalation triggers are **proposed, not measured**: 017 found no task that separated the
two models (section 11).

```yaml
id: 12gb-bonsai-strata
version: 2
summary: Bonsai 2 27B fast default, escalation to Strata Flash-Next Coder, one 12 GB card.
hardware: {gpu: nvidia-rtx-3060, vram_mib: 12288, ram_gib: 64, single_gpu: true}

backends:
  bonsai:
    engine: llama.cpp
    build: {repo: https://github.com/PrismML-Eng/llama.cpp, ref: 6bfcd79a}
    models:
      - file: bonsai2-27b/Ternary-Bonsai-2-27B-PTQ1_0-mtp.gguf
        sha256: 83a396ee218c36e5ed88205eccb940a71549d9a72a3d020cc2713f94a78f70f0
        source: https://huggingface.co/sudoingx/Ternary-Bonsai-2-27B-PTQ1_0-MTP-GGUF/resolve/f04a3bd22b7b482675663e99efaba6719347b419
      - file: bonsai2-27b/Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf
        sha256: 6807ede61d570bb86ba34b756a0fa109edc33668604de867c6ea6d8f1d631903
        source: https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf/resolve/b072e1d3b35a0a630cece372c2127528e0994386
    port: 18180
    health: /health
    capabilities: [text, vision]          # 017: 13/14 layout, 8/9 text with the mmproj
    launch:
      argv: [/opt/taos/engines/llama.cpp-prismml/bin/llama-server,
             -m, "{models[0]}", --mmproj, "{models[1]}", --alias, bonsai2-27b,
             --host, 127.0.0.1, --port, "{port}", -c, "131072", -np, "1", -ngl, "99",
             --jinja, -fa, "on", -ctk, q4_0, -ctv, q4_0, -ctkd, q4_0, -ctvd, q4_0,
             --spec-type, draft-mtp, --spec-draft-n-max, "1", --reasoning-effort, medium,
             --cache-ram, "8192", --ctx-checkpoints, "32", --metrics, --no-webui]
      env: {GGML_CUDA_BATCH_INVARIANT: "1"}   # 017: parity at temp 0; unset is 2.6x faster at 126K, parity untested
    memory: {vram_mib: 10822}                 # 017 measured peak with mmproj, after bench
    load_timeout_s: 60
    stop_grace_s: 10
  strata:
    engine: strata
    build: {repo: https://github.com/jaylfc/strata-12gb, ref: s12-040}
    models:
      - file: coder-IQ1_M/Qwen3.8-Flash-Next-GSQ-RCO-IQ1_M-00001-of-00002.gguf
        sha256: TODO   # record in lab inventory before this recipe can load
      - file: coder-IQ1_M/Qwen3.8-Flash-Next-GSQ-RCO-IQ1_M-00002-of-00002.gguf
        sha256: TODO
    port: 18181
    health: /health
    capabilities: [text, vision]          # vision encoder hot-loaded per image (015)
    launch:
      argv: [/opt/taos/engines/strata/.venv/bin/python, /opt/taos/engines/strata/serve/server.py,
             --config, "{recipe_dir}/strata.json", --port, "{port}", --engine, strata]
      env: {STRATA_ARENA_LOCK: "0"}
      # strata.json: --native/--ple-gguf (files above), --expert-cache auto, --spec 4,
      # --max-context 262144, --kv int8, --kv-resident 32768, --kv-persist, --vision,
      # --vram-reserve-mib 700, --resident-budget-gib, "vision": {"hot": true, "idle_unload_s": 120}
    memory: {vram_mib: 11800}
    load_timeout_s: 180      # ~85 s measured load
    stop_grace_s: 60         # must exceed the #751 KV persist save on shutdown

roles:
  default:    {backend: bonsai, model: bonsai2-27b}
  escalation: {backend: strata, model: qwen3.8-flash-next-coder}
  vision:     {resident_if_capable: true}   # never swaps for an image; see 4.4
  side:       {model_ref: side-model}       # an existing taOS model name, not GPU-managed

aliases:
  taos-coder: default        # planned per conversation: default or escalation
  taos-coder-heavy: escalation
  taos-side: side

routing:
  conversation_key: header             # x-taos-conversation; prefix_hash is opt-in (4.2)
  escalation_grant: taos-coder-heavy   # callers must may_use() this alias to cause a swap
  escalate_when:                       # any one, evaluated on the incoming request
    tool_failures: {consecutive: 3}
    loop: {identical_calls: 3}
    context_tokens_over: 120000        # Bonsai window is 131072
    explicit: true                     # x-taos-route: escalate (gated, 4.3)
  deescalate_when:
    explicit: true                     # x-taos-route: default
    successes_after_escalation: 6
    idle_s: 600                        # enforced by a sweep, not only on requests (4.5)
  min_dwell_s: 300
  serve_default_on_escalation_backend: true
  rate_limits: {swaps_per_caller_per_hour: 4, swaps_per_hour: 12}
  swap: {queue_max: 8, queue_wait_s: 150, retry_after_s: 150, cooldown_after_failure_s: 900}

repairs:
  bonsai:
    harness: oh-my-pi 18.6.1
    transforms:
      hashline: {status: experimental, evidence: "lab 017: 5 of 19 tool errors are split or bare edit headers; A/B pending"}
  strata:
    harness: oh-my-pi 18.6.1
    transforms:
      aliases:      {status: experimental, evidence: "lab 004 failure mining; A/B pending"}
      hashline:     {status: experimental, evidence: "lab 004: 135 failed edits of 563; A/B pending"}
      eval_browser: {status: experimental, evidence: "lab 004: 146 failed eval calls of 376; A/B pending"}

harness_hints:
  oh-my-pi:
    bonsai: {temperature: 1.0, top_p: 0.95, top_k: 20, min_p: 0.05,
             reasoning_effort: medium, thinking_budget: 3072, max_tokens: 7168}   # lab "medium-3k", as benched in 017
    strata: {reasoning_effort: low, thinking_budget: 1536}   # live setting; lab 002: low-1.5k and medium-3k within noise

results:
  - {what: "omp bench 8 tasks, Bonsai alone", pass: "8/8", min_per_pass: 4.56, tool_errors: 19,
     link: "omp-strata-lab experiments/017-bonsai2-27b"}
  - {what: "omp bench 8 tasks, Strata alone (omp 18.6.1)", pass: "8/8", min_per_pass: 5.1, tool_errors: 12,
     link: "omp-strata-lab experiments/009-upstream-trials"}
  - {what: "Bonsai decode at 126K, MTP, BI unset", tok_s: 41.7, link: "omp-strata-lab experiments/017-bonsai2-27b"}
  - {what: "escalation passes per hour", value: null, link: "omp-strata-lab experiments/018 (approved, not started)"}
```

## 4. Recipe-driven resolve

### 4.1 Seam and names

One new module, `llm_gateway/recipes.py`, and a hook in `router.chat_completions` between the
`taos-default` expansion and `find_routes`:

```python
plan = await recipes.plan(state, name, body, request.headers, caller)   # None when no recipe
if plan is not None:
    routes = plan.routes            # synthetic Route objects for the chosen backend
```

`recipes.plan` returns `None` when no recipe is active or `name` is not one of its aliases, and
the existing path runs untouched. `/models` (`list_models`) appends the aliases the caller may
use. Alias permission follows the `alias_grant` rule for `taos-default`: the caller needs
`may_use(alias)`, never the hidden backend names. A backend's route is a synthetic `Route` with
`provider="openai"`, `api_base` its loopback port, `backend_name="recipe:<id>/<backend>"` and
`backend_type` the engine, so `forward` sends it like any OpenAI-compatible route.

### 4.2 Conversation key

The **`x-taos-conversation` header is the main path**: the harness sends a stable, unique id per
session (the lab's omp extension does). The key is `(caller_id, header value)`, so one caller
cannot read or steer another caller's state by reusing its id.

`prefix_hash` (`sha256(caller_id + first system message + first user message)`) is opt-in per
recipe and documented as lossy: **it merges parallel runs** that share a prompt (a bench run's
repeats, subagents spawned from one template, two agents of one caller started on the same task),
so one run's failures can escalate or hold the other. Compaction or a rewritten first message
also starts a new key. It is off in the built-in recipe.

With no usable key the request is planned **statelessly**: only triggers computable from that
request's own `messages` apply (context size, image input, tool failures and loops visible in the
history), and no escalation hold is recorded, so it never keeps the escalation backend resident.

State is an in-memory bounded LRU (2,000 conversations, 6 h entry expiry), like callshim's
`State.originals`. Per key: current role, `since`, failure and success counters, recent tool-call
signatures (name plus argument hash), the hold flag and repair originals (section 6). Lost state on
restart is acceptable: the next request recomputes triggers from its `messages`.

### 4.3 Who may cause a swap

Any swap starts from a request, so escalation is gated on the caller, not just on the request:

- **Grant.** A request may move its conversation to `escalation`, by any trigger, only when
  `caller.may_use(routing.escalation_grant)` is true. A caller without it is planned on
  `default` whatever its messages say (a crafted history of fake tool failures or a padded prompt
  cannot force a swap). If its context exceeds the default window it gets the backend's own
  400, not a swap.
- **Explicit header.** `x-taos-route: escalate` from a caller without the grant is refused with
  `403 {"code": "escalation_not_permitted"}` before any planning. `x-taos-route: default` is
  always allowed (it only releases that caller's own hold).
- **Scope.** Only `agent`, `session` and admin callers count as identities here; a `node` caller
  never escalates. The header affects only the caller's own conversation key.
- **Rate limits.** A token bucket per `caller_id` (`swaps_per_caller_per_hour`) and a global one
  (`swaps_per_hour`) count swaps a caller *caused*. An explicit escalate over the limit gets
  `429` through `errors.rate_limit_error` with `retry-after`; an automatic trigger over the limit
  stays on the resident backend and records `model.route` with `reason="escalation_rate_limited"`.

### 4.4 Triggers and vision

Evaluated only at request boundaries, on the incoming body, after the grant check:

- `tool_failures`: trailing `role: tool` messages matching the harness's error markers (defined
  per harness in the loader, not as free regex in YAML);
- `loop`: the same tool-call signature N times in the recent assistant turns;
- `context_tokens_over`: a chars/4 estimate (as `forward._conservative_budget_estimate`),
  refined by the last reported `usage`;
- `explicit`: the gated header above.

**Vision never triggers a swap.** A request with an `image_url` part goes to the resident backend
when its `capabilities` include `vision`. Otherwise it goes to a non-GPU `vision` role
(`model_ref`) if the recipe names one, else it gets `400 {"code": "vision_unavailable"}`.

### 4.5 De-escalation, holds and idle release

A conversation on `escalation` holds the escalation backend. It releases the hold on an explicit
`x-taos-route: default`, after `successes_after_escalation`, or after `idle_s` with no request.
The idle case is enforced by a **sweep** every 30 s that clears holds whose last request is older
than `idle_s`, so a conversation that simply stops sending does not pin the GPU for the 6 h entry
lifetime. The swap back to `default` happens when no hold remains and `min_dwell_s` has passed.
While the escalation backend is resident, `serve_default_on_escalation_backend` lets
default-role requests run on it instead of forcing two swaps, subject to 6.4.

## 5. Swaps through LifecycleManager (Q1)

### 5.1 Registration

On activation the recipe's GPU backends are registered **with the catalog only**: a new
`BackendCatalog.register_managed(entries)` adds them to the catalog's own `_backends_config` copy
with `auto_manage: true`, `keep_alive_minutes: 0` (the recipe controller owns stops) and a
`managed_by: recipe:<id>` marker. They are never written to `config.backends`, so
`litellm_config.build_model_list` and every existing route are unchanged. Deactivation removes
them after stopping them.

### 5.2 Changes LifecycleManager needs

1. **argv start.** `start()` today runs `start_cmd` through `create_subprocess_shell` and waits
   for it to exit. Recipe backends supply `start_argv` instead, run with
   `create_subprocess_exec` (no shell). Because `start()` waits for the command to finish, the
   argv is wrapped in a transient systemd unit:
   `systemd-run --user --unit=taos-recipe-<id>-<backend> --collect -p TimeoutStopSec=<stop_grace_s> --setenv=K=V -- <argv>`,
   which returns once the unit starts. `stop_argv` is `systemctl --user stop <unit>`, which
   waits up to `stop_grace_s` (Strata's KV persist save) and then kills the process group.
2. **A real drain.** Add `BackendCatalog.in_flight_count(name)` backed by a counter the gateway
   increments in `forward._record_request_start` and decrements in `_record_request_finish`
   (both already run exactly once per attempt). `_wait_for_drain` then does what its docstring
   says. This also fixes the drain for existing auto-managed backends.
3. **Health.** `_probe_health` accepts JSON `status` in `ok|healthy|running`; llama-server's
   `/health` returns `{"status": "ok"}`. Strata's shape is checked in the lab; a recipe may
   declare a different `health` path.
4. **Per-backend timeouts** from the recipe (`startup_timeout_seconds` = `load_timeout_s`).
5. **Skip keep-alive** for `managed_by: recipe:*` entries in `notify_task_complete`.

### 5.3 Lease identity (Q2)

The gateway claims as a **dedicated identity**, `@taos-llm-gateway`: a service record in the
agent registry, created at startup, with only the `a2a_send` / `a2a_receive` scopes the lease
routes check. Its token never leaves the controller. The claim logic in
`routes/a2a_gpu_lease.gpu_claim`, `gpu_release` and `gpu_renew` moves into a service function
that both the routes and the controller call in-process. The controller holds one claim for "the
recipe's resident backend"; a re-CLAIM by the same identity replaces it (`open_claims`), and
`evaluate_admission(replace_own=True)` admits the new figure without charging the old twice.

### 5.4 Sequence

Old is the resident backend, new is the target. One swap at a time.

1. **Gate.** Mark `old` closed to new requests; arrivals join the swap queue (5.6). Record
   `model.route` with `reason="swap"`.
2. **Drain.** `drain_and_stop(old)` waits on `in_flight_count` up to 60 s. An in-flight stream is
   never cut: on drain timeout the swap is abandoned and `old` reopens.
3. **Stop.** `stop_argv` (systemd stop, up to `stop_grace_s`). Record `model.unload`.
4. **Claim.** CHECK, then re-CLAIM with `new.memory.vram_mib`. The claim comes **before** the
   load because it is the admission check (#893); the same holder re-claims, so a peer never sees
   the card unclaimed mid-swap. A live `free_mb` probe confirms `old` released its VRAM.
5. **Load.** `LifecycleManager.start(new)`, health within `load_timeout_s`. Record `model.load`.
6. **Open.** Re-plan the queue (5.6). Renew the claim on the lease's schedule while resident.

### 5.5 Failure and rollback

- **Claim refused** (another holder or not enough VRAM): `new` is not loaded. Re-CLAIM with
  **`old.memory.vram_mib`** (the claim now matches what will be resident again), then start
  `old`. Escalation enters `cooldown_after_failure_s`; holds are cleared. If a peer holds the
  card, post `[GPU REQUEST]` and answer the queue with 503.
- **`new` fails to start or times out:** stop it, re-CLAIM with `old.memory.vram_mib`, start
  `old`. The claim is restored *before* the reload so it never understates what is resident.
- **`old` also fails:** release the claim, mark the recipe `degraded`, answer 503 until an admin
  acts or the next permitted request retries a cold start of `default`.
- **Bus channel unreadable:** the lease fails closed (503), and so does the swap.

### 5.6 What clients see, and re-planning the queue

Requests that arrive during a swap wait in a bounded queue (`queue_max`, `queue_wait_s`, Q5).
Each queued entry keeps **its own** `GatewayCaller`, body and headers. When the swap ends, every
entry is re-planned with `recipes.plan` under its own caller and grants, not those of the request
that triggered the swap:

- if its plan is servable by the now-resident backend, it runs;
- if it would need another swap, that swap goes through the same grant and rate-limit checks as
  a fresh request, under that caller's identity;
- a caller without the escalation grant is never served by the escalation backend through the
  queue: it waits for the swap back or gets the 503 below.

Past either bound, or on a failed swap, the answer is `503 {"error": {"code": "backend_swapping"}}`
with `retry-after` (`GatewayError(503, ..., headers=...)`). The example sets `retry_after_s` equal
to `queue_wait_s` so a retry lands after the queue window, not inside it. The gateway does not
send a 200 and SSE keep-alives before a swap is known to succeed, because a started stream cannot
fail over (`_stream_with_retry`).

## 6. Repair stage in forward

Optional, per backend and harness. Q6: port callshim's `proxy.rewrite_call`, `request.prepare`
and `state.State` into `llm_gateway/repair/` **if its license allows**. callshim has no LICENSE
file today; as its sole author, jaylfc first adds an AGPL-3.0-compatible license there, and the
port lands after that.

- **Only `validated` transforms run** (callshim's `{"status": "validated", "evidence": ...}`);
  `experimental` runs only under a lab flag. Today all are `experimental`, so the example runs none.
- **Where.** Inside the per-attempt functions, so failover re-applies the right profile:
  non-streaming in `forward._chat_completion_one` after `_mirror_choices(data, "message")`;
  streaming in `_event_stream_for_route` next to `_mirror_choices(chunk, "delta")`, buffering
  `tool_calls` deltas per index until `finish_reason` or `[DONE]` and emitting one rewritten
  delta, as callshim `_relay_stream` does. Content and reasoning pass through unbuffered.
- **Request side:** before the POST, restore earlier rewritten calls in `messages` to the model's
  originals (kept per conversation by tool-call id), so the prompt cache stays an exact prefix.
- A transform that raises is skipped and logged; non-JSON arguments are left alone; accounting is
  unchanged. Not applied to Anthropic routes, embeddings, stt or tts.

## 7. Config and flags

- `TAOS_LLM_RECIPE` env, else `server.llm_recipe` in `config.yaml`, else off (the
  `llm_gateway.agent_port` precedence). Unset means today's behaviour.
- While experimental, the code also requires `TAOS_LLM_RECIPES_EXPERIMENTAL=1`.
- `TAOS_LLM_REPAIR=0` disables repair; `TAOS_LLM_REPAIR_EXPERIMENTAL=1` is the A/B-only switch.
- Activation: at startup, and on admin-only `POST /api/llm/recipe/activate` (session admin, CSRF
  as other admin routes). It validates the file, probes VRAM against `hardware.vram_mib`, checks
  every model sha256 (cached by path, size and mtime), and refuses on any mismatch.

## 8. Observability

Events reuse the stable `model_activity` vocabulary: `model.route` with `reason` `escalation`,
`deescalation`, `swap`, `escalation_refused` or `escalation_rate_limited` and a `detail` of
recipe, roles, trigger and a short conversation hash; `model.unload` / `model.load` around swaps
with durations. The feed is a non-durable ring, so swap outcomes, refusals and activations also
go to `SystemEventStore`. Repair before/after text goes only into the caller's own trace payload.
Metrics: swaps by outcome and cause, phase durations (drain, stop, claim, load), queue depth and
wait, 503 `backend_swapping`, 403 and 429 refusals by caller kind, time per role, rewrites by
transform.

## 9. Security

- Recipes are trusted config: loaded only from package data and `<data_dir>/recipes/`, never
  from an API body, an agent, the bus or the 7838 listener.
- No shell: `start_argv` via `create_subprocess_exec`; placeholders filled from the recipe only;
  paths confined to the roots in section 3; backends must bind 127.0.0.1.
- Swaps are gated by grant, scope and rate (4.3), and queued requests are re-planned under their
  own caller (5.6). Headers never name a backend, file or command.
- The `@taos-llm-gateway` identity holds only the lease scopes; its token never leaves the process.
- sha256 verification before any load.

## 10. Testing

- **Invariant:** with no recipe, `tests/test_llm_gateway.py`, `test_anthropic_gateway.py`,
  `test_llm_gateway_embeddings.py`, `test_llm_gateway_stt.py` and `test_llm_gateway_tts.py` pass
  unchanged, and a new test asserts `recipes.plan` returns `None` and no swap code runs.
- **Escalation gate (refusal tests):** a caller without `taos-coder-heavy` sending
  `x-taos-route: escalate` gets 403 and no swap starts; the same caller with a forged history of
  failed tool calls and a padded prompt stays on `default`; a granted caller over its bucket gets
  429 with `retry-after`; a `node` caller never escalates. Each declared with
  `@pytest.mark.guards(...)` and a `replace` pair that removes the grant check, which must fail.
- **Queue re-plan:** a swap triggered by a granted caller does not serve a queued ungranted
  caller on the escalation backend; a queued request that needs another swap is rate-limited
  under its own identity.
- Keys: parallel runs with different `x-taos-conversation` ids keep separate state; the
  documented merge under `prefix_hash` is pinned by a test; no key means no hold.
- Holds: the idle sweep releases a silent conversation's hold and the swap back follows dwell.
- LifecycleManager: argv start without a shell; `in_flight_count` makes `_wait_for_drain` wait;
  rollback re-claims `old.memory.vram_mib` before reloading; double failure releases the claim.
- Loader: schema, sha256 and path-root refusals, built-in id collision, package-data lookup.
- Repair: ported callshim units; split tool-call stream; failover re-applies the right profile.

## 11. Rollout

1. **This spec** (direction approved; this revision addresses the review).
2. **Lab prototype** in omp-strata-lab experiment 018 (approved to start). Per queue.md it uses
   llama-swap as the swap machinery with the policy above, and the omp extension sends
   `x-taos-conversation` and `x-taos-route`. It must measure swap time each way, time to first
   token after a swap, omp's request timeout (bounds `queue_wait_s`), and whether escalation
   beats both single-model arms in passes per hour on a task set that separates them. Plus the
   callshim `hashline` A/B for Bonsai with omp.
3. **taOS code PRs**, small, behind `TAOS_LLM_RECIPES_EXPERIMENTAL`: (a) loader, schema and the
   built-in as package data; (b) `LifecycleManager` argv start, in-flight drain, `register_managed`,
   and the lease service extraction with the gateway identity; (c) planner, gate and resolve hook;
   (d) swap controller and queue; (e) repair stage, after callshim is licensed.
4. Flag removed after a week of the whole stack on the lab's Cinderline goal (019's last gate).

## 12. Decisions and review findings

| Item | Decision or fix | Where |
|---|---|---|
| Q1 swap machinery | Build on `LifecycleManager`; `LazyBackendProxy` unused | 5 |
| Q2 lease identity | Dedicated `@taos-llm-gateway` registry identity | 5.3 |
| Q3 conversation key | `x-taos-conversation` header first; prefix hash opt-in | 4.2 |
| Q4 replanning | The harness owns replans | 2 |
| Q5 swap wait | Bounded queue, then 503 with `retry-after` | 5.6 |
| Q6 callshim | Port once callshim carries a compatible license | 6 |
| Q7 built-ins | Package data under `tinyagentos/llm_gateway/recipes/builtin/` | 3 |
| Lead 1 | Escalation gated by grant and scope, rate-limited, refusal tests | 4.3, 10 |
| Lead 2 | Prefix-hash merging of parallel runs documented; header is the main path | 4.2 |
| Lead 3 | Built-ins as package data, not `data/` | 3 |
| Lead 4 | Queued requests re-planned with each caller's own grants | 5.6 |
| Bot findings | Rollback restores the old claim; idle sweep releases holds; vision needs a capable backend; `retry_after_s` equals `queue_wait_s`; companion paths via `{recipe_dir}` | 5.5, 4.5, 4.4, 3 |
