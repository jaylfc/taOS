# Design: recipe-driven routing for llm_gateway

**Status:** Draft for review (spec first, then a lab prototype, then code PRs).
**Date:** 2026-10-06
**Supersedes:** nothing in code. `docs/design/plan-llm-proxy.md` (the LiteLLM-era plan) is
historical; there is no G2 router design doc, so this is the first one.

## 1. Context: what the gateway does today

Read from `tinyagentos/llm_gateway/` on `dev`:

- `router.chat_completions` validates the body, checks `GatewayCaller.may_use`, expands
  `taos-default` through `resolve.default_chat_model`, then takes
  `find_routes(routing_table(state), name)`. The table is `litellm_config.build_model_list`,
  rebuilt per request. `resolve.resolve()` exists but the route inlines the same steps.
- Dispatch is by `Route.provider`: `anthropic` goes to `anthropic.chat_completion_anthropic` /
  `chat_completion_stream_anthropic`; OpenAI-compatible and Ollama-shaped routes go to
  `forward.chat_completion` / `chat_completion_stream`.
- **Failover exists, but only within one model name.** `forward._call_with_retry` and
  `_stream_with_retry` try every route that shares the requested `model_name`, in priority
  order, on 5xx, timeout or unreachable. A failed backend sits in a 60 s cooldown
  (`_put_in_cooldown`), a stream is retried only before its first byte, and each move records a
  `model.route` event (`_record_route_change`, reason `failover`).
- **What does not exist:** routing across model names, escalation to a stronger model,
  de-escalation, or any coordination of which model is resident on a GPU.
- `stt.py` and `tts.py` are explicitly "no fallback of any kind" and import nothing from chat
  routing. `embeddings.py` reuses the chat failover.
- `listener.create_agent_listener_app` (host port 7838, `DEFAULT_AGENT_PORT`) is an allowlist
  that rewrites `/v1/models`, `/v1/chat/completions` and `/v1/embeddings` onto the main app.

Building blocks outside the package:

- `tinyagentos/lazy_backend_proxy.py` `LazyBackendProxy`: a raw TCP proxy that runs `start_cmd`
  (via `shlex.split`, no shell) on the first connection, polls `health_url` for up to
  `_COLD_START_TIMEOUT` (120 s), and stops the process after `idle_timeout_seconds` with no
  connections. It has no production caller today (only `tests/test_lazy_backend_proxy.py`).
- `tinyagentos/lifecycle_manager.py` `LifecycleManager`: wired as `app.state.lifecycle_manager`
  in `app.py`; `start`, `drain_and_stop` (waits up to 60 s for in-flight work) and a keep-alive
  timer that `forward._notify_lifecycle` resets after each completion.
- `tinyagentos/gpu_lease.py`: the pure half of the A2A GPU lease (#893): parse/render of
  `[GPU CLAIM|RELEASE|REQUEST|CHECK]` lines, `open_claims` (fold keyed by node and identity, a
  re-CLAIM replaces), and `evaluate_admission` (another holder blocks outright; otherwise need vs
  free VRAM; `replace_own` for an idempotent re-claim). `routes/a2a_gpu_lease.py` is the HTTP
  half: it pairs a bus line with a cluster lease (`cluster_manager`, TTL 300 s default, 3600 s
  max), reads VRAM via `system_stats.read_nvidia_vram`, and fails closed (503) when the bus
  channel cannot be read.

The motivating stack is in the omp-strata lab (experiments 015, 017, 019): on one 12 GB RTX
3060, Bonsai 2 27B on the PrismML llama.cpp fork is the fast default (8/8 on the lab bench at
4.56 min per pass, about 42 tok/s decode at depth), and Strata (Qwen3.8-Flash-Next Coder) is the
stronger, slower model with a 262K window and hot-loaded vision. Only one fits at a time.

## 2. Goals and non-goals

Goals:

1. A declarative **recipe** that describes a validated model stack for one hardware class, and
   that the gateway can execute: which backends exist, which role each serves, when to switch.
2. Recipe-driven resolve: a client asks for a role alias; the router picks the backend per
   request, keeping per-conversation state so a conversation escalates and returns cleanly.
3. Safe swaps on a single GPU: drain, unload, admission through the GPU lease, load, verify,
   with rollback, and a defined answer for clients that arrive mid-swap.
4. An optional, per-model and per-harness **repair stage** (callshim-style tool-call repair) that
   runs only transforms A/B-validated for that exact pair.

Non-goals:

- **No behaviour change without an active recipe.** With no recipe configured, every code path
  above runs exactly as today, byte for byte. This is a tested invariant (section 9).
- stt and tts stay no-fallback; recipes never touch `stt.py`, `tts.py` or their routes.
- The Anthropic translator, embeddings routing and the 7838 listener allowlist are unchanged.
  A recipe role may name an Anthropic or cloud route, but no repair runs on it.
- No multi-GPU placement, no cross-node scheduling (that is `scheduler/` and `cluster/`), no
  recipe marketplace, and no recipe editing from the UI or API in this phase.

## 3. Recipe format

YAML. Built-ins ship in the repo at `data/recipes/builtin/*.yaml` (tracked, replaced on update).
Operator recipes go in `data/recipes/local/*.yaml` (gitignored, like `data/config.yaml`). The
file name stem is the recipe id; a local recipe with a built-in's id is refused, not merged.

Top-level keys:

| Key | Meaning |
|---|---|
| `id`, `version`, `summary` | Identity. `version` is an integer bumped on any change. |
| `hardware` | `vram_mib`, `ram_gib`, `gpu` (vendor and class), `single_gpu: true`. The router refuses to activate a recipe whose `vram_mib` exceeds the probed card. |
| `backends` | Named engines. Each: `engine`, `build` (repo, ref, binary path), `models` (files with `sha256` and source URL at a pinned revision), `port`, `health`, `launch` (argv list and env), `memory` (`vram_mib` claimed when resident, safety flags), `load_timeout_s`, `stop_grace_s`. |
| `roles` | `default`, `escalation`, `vision`, `side`. Each points at a backend (or at an existing taOS model name via `model_ref`) and gives the upstream model id. |
| `aliases` | Model names clients send, mapped to roles. |
| `routing` | Escalation triggers, de-escalation, `min_dwell_s`, swap queue limits. |
| `repairs` | Per backend and harness: transforms with `status` and `evidence`. Only `validated` ones run. |
| `harness_hints` | Sampling, thinking effort and budget, max tokens, per harness. Advisory, not enforced. |
| `results` | Measured numbers with links to the lab evidence. |

Rules: every model file has a sha256 (the loader refuses an unpinned file); `launch.argv` is a
list, never a shell string; every path is absolute or relative to `data/`.

### 3.1 Example: `data/recipes/builtin/12gb-bonsai-strata.yaml`

Bonsai values are the lab's (017 `fetch.sh`, `serve-bonsai.sh`). Strata file hashes are not
recorded in the lab yet and are marked `TODO`; the loader would refuse the file until they are
filled. Escalation triggers are **proposed, not measured**: 017 found no task that separated the
two models (section 10).

```yaml
id: 12gb-bonsai-strata
version: 1
summary: Bonsai 2 27B fast default, escalation to Strata Flash-Next Coder, one 12 GB card.
hardware: {gpu: nvidia-rtx-3060, vram_mib: 12288, ram_gib: 64, single_gpu: true}

backends:
  bonsai:
    engine: llama.cpp
    build: {repo: https://github.com/PrismML-Eng/llama.cpp, ref: 6bfcd79a,
            binary: /opt/taos/engines/llama.cpp-prismml/bin/llama-server}
    models:
      - file: models/bonsai2-27b/Ternary-Bonsai-2-27B-PTQ1_0-mtp.gguf
        sha256: 83a396ee218c36e5ed88205eccb940a71549d9a72a3d020cc2713f94a78f70f0
        source: https://huggingface.co/sudoingx/Ternary-Bonsai-2-27B-PTQ1_0-MTP-GGUF/resolve/f04a3bd22b7b482675663e99efaba6719347b419
      - file: models/bonsai2-27b/Ternary-Bonsai-2-27B-mmproj-Q8_0.gguf
        sha256: 6807ede61d570bb86ba34b756a0fa109edc33668604de867c6ea6d8f1d631903
        source: https://huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf/resolve/b072e1d3b35a0a630cece372c2127528e0994386
    port: 18180
    health: /health
    launch:
      argv: [-m, "{models[0]}", --mmproj, "{models[1]}", --alias, bonsai2-27b,
             --host, 127.0.0.1, --port, "{port}", -c, "131072", -np, "1", -ngl, "99",
             --jinja, -fa, "on", -ctk, q4_0, -ctv, q4_0, -ctkd, q4_0, -ctvd, q4_0,
             --spec-type, draft-mtp, --spec-draft-n-max, "1", --reasoning-effort, medium,
             --cache-ram, "8192", --ctx-checkpoints, "32", --metrics, --no-webui]
      env: {GGML_CUDA_BATCH_INVARIANT: "1"}   # 017: output parity at temp 0; unset is 2.6x faster at 126K, parity untested
    memory: {vram_mib: 10822}                 # 017 measured peak with mmproj, after bench
    load_timeout_s: 60
    stop_grace_s: 10
  strata:
    engine: strata
    build: {repo: https://github.com/jaylfc/strata-12gb, ref: s12-040,
            binary: /opt/taos/engines/strata/.venv/bin/python}   # runs serve/server.py
    models:
      - file: models/coder-IQ1_M/Qwen3.8-Flash-Next-GSQ-RCO-IQ1_M-00001-of-00002.gguf
        sha256: TODO   # record in lab inventory before this recipe can load
      - file: models/coder-IQ1_M/Qwen3.8-Flash-Next-GSQ-RCO-IQ1_M-00002-of-00002.gguf
        sha256: TODO
    port: 18181
    health: /health
    launch:
      argv: [/opt/taos/engines/strata/serve/server.py, --config, recipes/12gb-bonsai-strata/strata.json,
             --port, "{port}", --engine, strata]
      env: {STRATA_ARENA_LOCK: "0"}
      # strata.json carries: --native/--ple-gguf (files above), --expert-cache auto, --spec 4,
      # --max-context 262144, --kv int8, --kv-resident 32768, --kv-persist, --vision,
      # --vram-reserve-mib 700, --resident-budget-gib, and "vision": {"hot": true, "idle_unload_s": 120}
    memory: {vram_mib: 11800, resident_budget_gib: 24}
    load_timeout_s: 180      # ~85 s measured load; KV persist save runs on stop
    stop_grace_s: 60         # must exceed the #751 shutdown save

roles:
  default:    {backend: bonsai, model: bonsai2-27b}
  escalation: {backend: strata, model: qwen3.8-flash-next-coder}
  vision:     {resident: true}          # whichever is resident: Bonsai mmproj or Strata hot-load (015)
  side:       {model_ref: side-model}   # an existing taOS model name, not GPU-managed

aliases:
  taos-coder: default        # the router picks default or escalation per conversation
  taos-coder-heavy: escalation
  taos-side: side

routing:
  conversation_key: [header, prefix_hash]
  escalate_when:             # any one, evaluated on the incoming request only
    tool_failures: {consecutive: 3}
    loop: {identical_calls: 3}
    context_tokens_over: 120000          # Bonsai window is 131072
    explicit: true                       # x-taos-route: escalate
  deescalate_when:
    explicit: true                       # x-taos-route: default
    successes_after_escalation: 6        # consecutive turns with no tool failure
    idle_s: 600
  min_dwell_s: 300
  serve_default_on_escalation_backend: true   # no swap back while Strata is resident and useful
  swap: {queue_max: 8, queue_wait_s: 150, retry_after_s: 90, cooldown_after_failure_s: 900}

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
  - {what: "escalation passes per hour", value: null, link: "omp-strata-lab experiments/019-combo-stack (gate pending)"}
```

## 4. Recipe-driven resolve

**Seam.** One new module, `llm_gateway/recipes.py`, plus a hook in `router.chat_completions`
between the `taos-default` expansion and `find_routes`:

```python
plan = await recipes.plan(state, name, body, request.headers, caller)   # None when no recipe
if plan is not None:
    routes = plan.routes            # synthetic Route objects for the chosen backend
```

`recipes.plan` returns `None` when no recipe is active or `name` is not one of its aliases, and
the existing path runs untouched. `/models` (`list_models`) appends the active recipe's aliases.

**Names.** Clients send an alias (`taos-coder`). Alias permission uses the same rule as the
`alias_grant` branch for `taos-default`: the caller needs `may_use(alias)`, not the hidden
backend names. An operator can point `taos-default` at `taos-coder` in the taOS agent settings,
so existing agents need no change. A backend's own route is synthesised as a `Route` with
`provider="openai"`, `api_base` the backend's loopback port, `backend_name="recipe:<id>/<backend>"`
and `backend_type` the engine, so `forward` sends it as it does any OpenAI-compatible route.

**Conversation key.** The gateway is stateless per request, so the router keys state by, in
order: an `x-taos-conversation` header if present; else
`sha256(caller_id + first system message + first user message)`, which is stable for a harness
that keeps its prefix (and is what the prompt cache already depends on). Compaction or a rewritten
first message starts a new key; the new key starts at `default` unless the context-size trigger
fires at once.

**State.** An in-memory, bounded LRU (2,000 conversations, entries dropped after 6 h idle), like
callshim's `State.originals`. Per key: current role, `since` (monotonic), consecutive failure and
success counters, the last N tool-call signatures (name plus argument hash), and repair originals
(section 6). Lost state on restart is acceptable: the next request recomputes triggers from its
`messages`.

**Triggers.** Evaluated only at request boundaries, on the incoming body:

- `tool_failures`: trailing `role: tool` messages whose content matches the harness's error
  markers (per harness, in the recipe loader, not free regex in YAML).
- `loop`: the same tool-call signature N times in the recent assistant turns.
- `context_tokens_over`: a chars/4 estimate of the prompt (the same estimate
  `forward._conservative_budget_estimate` uses), refined by the last reported `usage`.
- image input: any `image_url` content part routes to the `vision` role (here: the resident
  backend, so it never forces a swap).
- `explicit`: `x-taos-route: escalate|default` header, which a harness extension can set between
  turns (the lab's omp extension does this).

**De-escalation** needs `min_dwell_s` elapsed and one `deescalate_when` condition. Because only
one backend is resident, the swap target is global: a conversation that wants `default` while
another holds `escalation` is served by the resident escalation backend when
`serve_default_on_escalation_backend` is true (the stronger model serving an easy turn is cheaper
than two swaps). The GPU swaps back only when no conversation holds `escalation` and the dwell
has passed.

## 5. Swaps

**Owner.** A `SwapController` per active recipe, holding one `LazyBackendProxy` per GPU backend
(proxy port = backend port + 1000; the `Route.api_base` points at the proxy). It is the only
code allowed to start or stop recipe backends. Recipe backends are not in the `BackendCatalog`,
so `LifecycleManager` and `_notify_lifecycle` ignore them (the notify is skipped for
`recipe:` backend names to avoid a warning per request).

**Changes `LazyBackendProxy` needs** (it was written for one always-allowed backend):

1. A start gate: `start_allowed: Callable[[], bool]`. A stray connection must never start Strata
   while Bonsai is resident; when the gate is closed the proxy answers 503.
2. Public `ensure_started()` and `drain_and_stop(timeout)` that wait on `_active_connections`
   reaching zero before `_stop_subprocess`. Today `stop()` kills regardless, and as a TCP proxy
   it cannot see request boundaries, so the controller also tracks in-flight requests itself.
3. Per-instance cold-start timeout (today the module constant `_COLD_START_TIMEOUT = 120`, too
   short for a Strata cold load with KV restore) and an env mapping for `start_cmd`.
4. Backend stderr to a log file under `data/logs/recipes/` instead of `DEVNULL`.
5. Its 503 body shaped like `errors.openai_error_body`, with `retry-after`.
6. `idle_timeout_seconds=0` while a conversation holds the backend (the existing `_restart_idle_timer`
   already skips the timer for 0), and the recipe's idle TTL once nothing does.

**Lease identity.** The controller holds one GPU claim for "the recipe's resident backend". Its
`(node, identity)` key means a re-CLAIM replaces the previous one (`open_claims`), and
`evaluate_admission(replace_own=True)` admits the new figure against the card without charging
the old one twice. The controller calls the claim logic in-process (the body of
`routes/a2a_gpu_lease.gpu_claim` / `gpu_release` / `gpu_renew` moves into a small service
function both the routes and the controller call); it never HTTP-calls its own controller.

**Sequence** (old = resident backend, new = target):

1. **Gate.** Close admission to `old` for new requests; requests that arrive now enter the swap
   queue (below). Record `model.route` with `reason="swap"`.
2. **Drain.** Wait for `old`'s in-flight requests, up to `drain_timeout_s` (default 60, matching
   `lifecycle_manager._DRAIN_TIMEOUT_SECONDS`). An in-flight stream is never cut; past the timeout
   the swap is abandoned and `old` reopens (a swap is never worth killing a running answer).
3. **Stop.** `drain_and_stop` on `old`'s proxy; wait for process exit up to `stop_grace_s` (Strata
   needs its KV persist save to finish). Record `model.unload`.
4. **Claim.** CHECK then re-CLAIM with `new.memory.vram_mib` and `expires` from the lease TTL.
   This happens **before** loading, not after: the claim is the admission check (another holder
   blocks outright), so loading first would recreate the #893 silent co-load. Because the same
   holder re-claims, a peer never sees the card unclaimed mid-swap. A live `free_mb` probe also
   confirms `old` released its VRAM.
5. **Load.** `ensure_started()` on `new`; health poll up to `load_timeout_s`. Record `model.load`.
6. **Open.** Admit the queue to `new`, renew the claim on the lease's schedule while resident.

**Failure and rollback.**

- Claim refused (another holder, or insufficient VRAM): do not load `new`; reload `old` (step 5
  with `old`), mark the escalation `cooldown_after_failure_s`, keep the conversation on `default`.
  If the claim was refused because a peer holds the card, the queue gets 503 and a
  `[GPU REQUEST]` is posted.
- `new` fails to start or times out: kill it, reload `old`. If `old` also fails, release the
  claim, mark the recipe `degraded`, and answer 503 until an operator acts or the next request
  retries a cold start of `default`.
- Bus channel unreadable: the lease route fails closed (503), and so does the swap (no load
  without a claim).
- A swap never runs while another is in progress; requests that would trigger a second swap join
  the queue and are re-planned when the first finishes.

**What clients see.** Requests that arrive during a swap wait in a bounded queue (`queue_max`,
`queue_wait_s`). Past either bound, or on a failed swap, they get an OpenAI-shaped
`503 {"error": {"code": "backend_swapping"}}` with `retry-after` from the recipe (90 s here),
built with `GatewayError(503, ..., headers={"retry-after": ...})`. `queue_wait_s` must stay below
the harness's own request timeout (omp's is measured in the lab before this is fixed). The
gateway does not send a 200 and SSE keep-alive comments before the swap is known to succeed,
because a stream that has started cannot be failed over (`_stream_with_retry`).

## 6. Repair stage in forward

Optional, per backend and harness, ported from callshim's `proxy.rewrite_call`, `request.prepare`
and `state.State` into `llm_gateway/repair/`. The harness is identified from a request header
set by the harness extension, falling back to the caller's agent framework; with no match, no
transform runs.

- **Only `validated` transforms run.** The status shape is callshim's
  (`{"status": "validated", "evidence": "..."}`); `experimental` runs only under a separate lab
  flag. Today every transform in callshim's one profile is `experimental`, so the example recipe
  runs none.
- **Where.** Inside the per-attempt functions, so a failover re-applies the right transforms for
  the backend that actually answers:
  - non-streaming: `forward._chat_completion_one`, after `_mirror_choices(data, "message")`,
    rewrite `choices[].message.tool_calls`;
  - streaming: `_event_stream_for_route`, in the SSE loop next to `_mirror_choices(chunk, "delta")`.
    `tool_calls` deltas are buffered per index until the choice's `finish_reason` or `[DONE]`,
    rewritten, and emitted as one delta, exactly as callshim `_relay_stream` does. Content and
    reasoning deltas pass through unbuffered, so text can reach the client before a tool call
    is rewritten; that is fine because rewrites only touch tool-call arguments.
  - request side: before the POST, restore each earlier rewritten call in `messages` to the
    model's original (kept in the conversation state by tool-call id), so the model sees its own
    tokens and the backend's prompt cache stays an exact prefix.
- **Safety.** A transform that raises is skipped and logged (callshim's rule); an argument
  string that is not JSON is left alone; usage and spend accounting are unchanged.
- Not applied to Anthropic routes, embeddings, stt or tts.

## 7. Config and flags

- `TAOS_LLM_RECIPE` env, else `server.llm_recipe` in `config.yaml`, else off. The same
  precedence as `llm_gateway.agent_port`. Empty or unset means no recipe and today's behaviour.
- During the prototype, code paths also require `TAOS_LLM_RECIPES_EXPERIMENTAL=1`.
  (`TAOS_LLM_GATEWAY` stays the logged no-op it is.)
- `TAOS_LLM_REPAIR=0` disables the repair stage while keeping routing; `TAOS_LLM_REPAIR_EXPERIMENTAL=1`
  is the A/B-only switch for `experimental` transforms.
- A recipe is activated at startup and on an admin-only `POST /api/llm/recipe/activate`
  (session admin, CSRF as other admin routes). Activation validates the file, probes VRAM against
  `hardware.vram_mib`, checks sha256 of every model file once (cached by path, size and mtime),
  and refuses on any mismatch.

## 8. Observability and security

**Events** reuse the stable `model_activity` vocabulary instead of adding names:
`model.route` with `reason` `escalation`, `deescalation` or `swap` and `detail`
(`recipe`, `from_role`, `to_role`, `trigger`, `conversation` as a short hash); `model.unload` and
`model.load` around swaps (with durations); `request.finish` unchanged. The feed is a
non-durable ring, so swap outcomes and recipe activations also go to `SystemEventStore`.
Repair rewrites are counted per transform; the before and after arguments go only into the
caller's own trace payload, never into logs or the shared feed.

**Metrics** (on the existing metrics surface): swaps by outcome, swap duration by phase (drain,
stop, claim, load), queue depth and wait, 503s with `backend_swapping`, escalations by trigger,
time per role, repair rewrites by transform.

**Security.**

- Recipes are trusted config, like `config.yaml`: loaded only from `data/recipes/builtin/` and
  `data/recipes/local/`, never from an API body, an agent, the bus or the 7838 listener.
- `launch.argv` is a list executed without a shell (`subprocess` with argv, as
  `LazyBackendProxy` does with `shlex.split`); `binary` and model paths must resolve under
  approved roots (`/opt/taos/engines`, `data/models`, `data/recipes`); placeholders are
  substituted from the recipe only, never from a request.
- Backends bind 127.0.0.1 only; the loader refuses any other `--host`.
- The `x-taos-route` and `x-taos-conversation` headers only choose among the active recipe's
  roles for that caller's own conversation; they cannot name a backend, a file or a command.
- sha256 verification before any load; a mismatch refuses the recipe.

## 9. Testing

- **Invariant:** with no recipe, `tests/test_llm_gateway.py`, `test_anthropic_gateway.py`,
  `test_llm_gateway_embeddings.py`, `test_llm_gateway_stt.py` and `test_llm_gateway_tts.py` pass
  unchanged, and a new test asserts `recipes.plan` returns `None` and no recipe module code runs.
- Loader: schema, sha256 refusal, unpinned file refusal, shell-string argv refusal, host refusal,
  path-root refusal, built-in id collision.
- Planner (pure, table-driven): each trigger, dwell, de-escalation, `serve_default_on_escalation_backend`,
  conversation keying.
- Swap controller with fake backends (the `_EchoServer` pattern from
  `tests/test_lazy_backend_proxy.py`) and a fake lease: happy path, claim refused, load timeout
  with rollback, double failure, drain timeout abandons, queue full gives 503 with `retry-after`.
- Repair: callshim's transform units ported; a streaming test with a fake SSE upstream that
  splits a tool call over many deltas; failover re-applies the right profile; history restore
  gives byte-identical prefixes.
- Guard tests declare `@pytest.mark.guards(...)` with a `replace` pair that reproduces the real
  defect (for example, loading before claiming), per CONTRIBUTING.
- Hardware validation is the lab's job (section 10), not CI's.

## 10. Rollout

1. **This spec** (review and agree).
2. **Lab prototype behind a flag, outside taOS.** In omp-strata-lab experiment 018, as queue.md
   proposes: llama-swap as the swap machinery (one exclusive group, Bonsai and Strata), the policy
   above in a small router, the omp extension setting `x-taos-route`. It must answer the open
   numbers: swap time each way, time to first token after a swap, whether escalation beats both
   single-model arms in passes per hour on a task set that separates them (017 found none), and
   omp's request timeout. Plus the callshim `hashline` A/B for Bonsai with omp (017 next gate).
3. **taOS code PRs**, each small, behind `TAOS_LLM_RECIPES_EXPERIMENTAL`: (a) recipe loader and
   schema with the built-in file; (b) `LazyBackendProxy` changes and the lease service extraction;
   (c) planner and resolve hook; (d) swap controller; (e) repair stage. Each with its changelog
   fragment and the doc gate satisfied.
4. Flag removed after a week of the whole stack on the lab's Cinderline goal (019's last gate).

## 11. Open questions for Jay

1. **Swap machinery:** extend `LazyBackendProxy` (as specified, it has no production caller and
   lacks drain and a start gate) or build on `LifecycleManager` plus `BackendCatalog`, which is
   already wired and has `drain_and_stop`?
2. **Lease identity:** which identity does the in-process router claim as? Admin actions are
   `@operator`; agents have registry ids; the gateway is neither. A dedicated `@taos-llm-gateway`
   bus identity?
3. **Conversation key:** is a header plus a prefix hash acceptable, or should harnesses be
   required to send `x-taos-conversation`?
4. **Replan before escalating:** queue.md suggests one replan steer on the first failure and
   escalation on the next. Is that the router's job (inject a message) or the harness's?
5. **Swap wait:** queue up to about 150 s, or 503 at once and let the harness retry?
6. **callshim code:** port it into `llm_gateway/repair/` under AGPL, or depend on callshim as a
   package?
7. **Recipe location:** `data/recipes/builtin/` and `data/recipes/local/`, or built-ins under
   `tinyagentos/` as package data?
