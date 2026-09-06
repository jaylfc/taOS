> **Status (2026-08-14): Not implemented.** The file paths referenced below are proposed, not present in the codebase.

# Unified GPU Work Queue — Design Specification

- **Issue:** #1864 ("Follow-up: model loads as evictable submit_gpu tasks (arbiter Option B)"), evolved per owner direction into a broader feature.
- **Status:** Draft for review
- **Date:** 2026-07-17
- **Builds on:** #185 (single VRAM authority, merged), #893/#894 (lease system + GPU arbiter), #1706 (atomic VRAM check-and-reserve)

---

## 1. Overview

taOS today has a GPU arbiter (`tinyagentos/scheduler/gpu_arbiter.py:71`) with VRAM-accounted admission, a priority queue, and eviction — but its front door, `GpuArbiter.submit_gpu` (`gpu_arbiter.py:304`), has **no production caller** (verified: no non-test reference outside `gpu_arbiter.py`). Model loads reserve VRAM directly against the shared `VramReservationManager` and **503 immediately** when VRAM is short (`tinyagentos/routes/models.py:414-423` rkllama, `models.py:674-684` ollama). Inference never touches the arbiter at all. Nothing in the codebase can unload a resident model, so "eviction" today cancels an asyncio task without freeing a single byte of VRAM.

This spec makes the arbiter the **single front door for all GPU-bound work** — model loads *and* inference — and makes it the **residency manager** for models on local GPU backends.

### Owner decisions (settled; designed around, not relitigated)

1. **One uniform queue.** Loads and inference both admit through it, always — not only under contention.
2. **The queue is the instrumentation point.** Every GPU op flows through it, so timing, model, VRAM, wait-time, and throughput data is captured centrally and fed to the existing audit/trace + benchmark layers.
3. **The queue is the residency manager.** A model is ACTIVE iff it has queued/running tasks; IDLE iff resident with none. Load on admit; when VRAM is short, unload the least-recently-used IDLE model. Never-evict-active falls out of queue state — no separate in-flight signal.
4. **Visible queue position.** A waiting chat shows "waiting — position N"; a queued pull shows "queued behind N". Uncontended → instant admission, nothing shown.
5. **Hot-path constraint.** Inference is latency-sensitive; the uncontended path must be a near-zero-overhead passthrough.

### Goals

- Replace the 503-on-insufficient-VRAM contract for model pulls with queue-and-wait (+ eviction of idle models).
- Route inference through the same admission point, with per-model concurrency limits and a fast path.
- Introduce the missing **unload primitive** (ollama/rkllama) and register it as the queue's eviction mechanism.
- Surface queue position to the Models app and the chat UI via the existing SSE event stream.
- Emit a per-op audit record for every GPU op through the existing trace/record hook.

### Non-goals

- **Not changing how a model generates tokens.** The queue does admission, ordering, and residency. Token generation, sampling, context handling, and streaming semantics stay entirely inside the backends.
- Not replacing the resource scheduler (`tinyagentos/scheduler/scheduler.py`), the cluster lease system, or LiteLLM routing.
- Not scheduling **cluster-worker** GPU work at the op level. Phase 1 governs the **local** GPU. The arbiter's existing cluster-admission path (`gpu_arbiter.py:363-392`) is untouched; per-worker queues are future work (see §10).
- Not covering image-generation backends (sd-cpp/iopaint/flux-fill) in Phase 1 — they are additional gateway targets later (§7 rollout), same mechanism.
- Not building a new telemetry pipeline. We call the existing `AgentTraceStore.record()` hook (`tinyagentos/trace_store.py:264`).

---

## 2. Architecture

### 2.1 Where inference actually flows today (verified)

There is no single controller-side inference call site. The real paths:

- **Deployed agents → LiteLLM proxy → backend, bypassing the controller.** The controller spawns LiteLLM as a subprocess on port 7834 (`tinyagentos/llm_proxy.py:70-91`) and writes each local backend's URL straight into the LiteLLM config as `api_base` (`tinyagentos/litellm_config.py:259-261`). Agent tokens then travel LiteLLM → ollama/rkllama directly; the controller only *observes* the call afterwards via the LiteLLM `CustomLogger` callback that POSTs an `llm_call` trace to `/api/trace` (`tinyagentos/litellm_callback.py:76-229`, `tinyagentos/routes/trace.py:45`).
- **Chat messages** enter at `POST` in `tinyagentos/routes/chat.py:266` and are routed by `AgentChatRouter.dispatch` (`tinyagentos/agent_chat_router.py:66`) to the agent's bridge session or an ACP turn — the agent process then calls LiteLLM. Again: the controller is not in the token path.
- **Direct backend callers inside the controller:** the benchmark runner POSTs `{backend_url}/v1/chat/completions` directly (`tinyagentos/benchmark/runner.py:246`); the cluster `TaskRouter.chat` POSTs to workers (`tinyagentos/cluster/router.py:41-45`). `routes/agent_model_api.py` (agent-as-a-model) drives a real one-shot agent turn via the opencode host-server seam and returns an OpenAI ChatCompletion envelope.

**Consequence:** "ALL inference flows through the queue" cannot be achieved by editing controller call sites — most inference doesn't pass through the controller. It requires a **choke point on the network path to the backend**.

### 2.2 The GPU gateway: one front door

New component: a streaming reverse proxy on the controller, `tinyagentos/routes/gpu_gateway.py`, mounted at:

```
/gpu/{backend_name}/{path:path}
```

- `generate_litellm_config` rewrites `api_base` for **local GPU LLM backends** (`LOCAL_TYPES` that are GPU-bound on this host — ollama, rkllama, llama-cpp, vllm, hailo-ollama; set from `tinyagentos/providers/__init__.py:20-58`) from the backend URL to `http://127.0.0.1:{port}/gpu/{backend_name}` (change at `litellm_config.py:259-261`). Cloud providers and remote workers are never rewritten.
- Controller-internal direct callers (benchmark runner, agent-as-a-model execution) switch from raw `backend_url` to the gateway URL — or, when running in-process, call the queue API directly and skip the HTTP hop.
- The gateway parses the target model from the request body (`model` field — present on `/api/chat`, `/api/generate`, `/v1/chat/completions`, `/api/pull`, `/api/embed`), admits through the queue, then streams the request/response bytes through unbuffered (same `X-Accel-Buffering: no` discipline as `tinyagentos/routes/event_stream.py:104-112`).
- **Auth:** LiteLLM and internal callers present the local token (the same `data/.auth_local_token` the LiteLLM callback already reads, `litellm_callback.py:30-47`), passed via a per-model `extra_headers` entry in the generated LiteLLM config. The gateway path is not cookie-exempt; it accepts local-token bearer auth only.
- Requests whose path has no model semantics (`/api/tags`, `/api/ps`, `/health`, `/api/version`) pass through with **zero** queue interaction.

This is the only design that satisfies decision 1 given the verified topology. The cost is one extra localhost HTTP hop for LiteLLM-routed inference; §4.3 budgets it.

### 2.3 Queue components

```
                 ┌────────────────────────────────────────────────┐
  ModelsApp ───► │ routes/models.py  (pull → GpuOp kind=load)     │
                 ├────────────────────────────────────────────────┤
  LiteLLM ─────► │ routes/gpu_gateway.py (infer → GpuOp kind=     │
  bench runner ► │  inference; pull-through → kind=load)          │
                 └───────────────┬────────────────────────────────┘
                                 ▼
                 ┌────────────────────────────────────────────────┐
                 │ GpuArbiter (extended) — single front door      │
                 │  • admission via shared VramReservationManager │
                 │  • priority queue + aging (§8.3)               │
                 │  • ResidencyManager (new, §3)                  │
                 │  • per-model concurrency (§4.2)                │
                 │  • trace + SSE emission (§5, §6)               │
                 └───────────────┬────────────────────────────────┘
                                 ▼
                     ollama / rkllama / llama-cpp …
```

We **extend `GpuArbiter` in place** rather than adding a sibling class: it is already wired in `app.py:1183-1191` with the shared ledger (`app.state.vram_reservation`, `app.py:1175-1176`), already owns the queue, eviction ordering (the Xid-62-safe cancel-await-then-release sequence, `gpu_arbiter.py:503-526`), pause/resume (`gpu_arbiter.py:150-182`), and stats.

### 2.4 The op shape: loads and inference as arbiter tasks

The scheduler `Task` (`tinyagentos/scheduler/types.py:76-93`) has `capability`, `payload`, `priority`, `submitter`, `estimated_vram_mb` — but **no model identity and no load/inference distinction**. `Capability` (`types.py:11-19`) classifies *work type* (llm-chat, embedding, …), which is orthogonal to load-vs-inference (you can load an embedding model, and run inference on a chat model). We therefore do **not** overload `Capability`; we add an explicit op kind.

New fields on `submit_gpu` (backward-compatible keyword args; existing tests unaffected):

```python
async def submit_gpu(
    self, task: Task, required_vram_mb: int = 0,
    evictable: bool = False, resource_id: str | None = None,
    required_gpu_arch: str | None = None,
    # NEW:
    op: str = "inference",          # "load" | "inference"
    model: str | None = None,       # backend model name, e.g. "qwen2.5:7b"
    backend_name: str | None = None,
) -> object: ...
```

`_QueuedGpuTask` (`gpu_arbiter.py:48-57`) gains the same three fields (all `compare=False`). Two op profiles:

| | `op="load"` | `op="inference"` |
|---|---|---|
| payload | run the pull/installer coroutine (today's `_install_and_record` body, `models.py:425-459`, or the gateway's proxied `/api/pull`) | forward the proxied request to the backend and stream the response |
| `required_vram_mb` | the model's VRAM estimate (`_estimated_vram_mb`, `models.py:152-167`, or caller-supplied `required_vram_mb`, `models.py:36`) | **0 if the model is resident** (weights already in VRAM; nvidia-smi already counts them); the model's estimate if a load must be triggered first |
| priority | `Priority.BACKGROUND` (30) by default (`types.py:22-31`) | `Priority.INTERACTIVE_USER` (10) for user-facing chat, `INTERACTIVE_AGENT` (20) for agent-initiated |
| evictable | n/a (loads don't get evicted-for; they may *cause* idle-unload) | default `False` (existing arbiter default, `gpu_arbiter.py:306`); room-making prefers idle-model unload over task preemption (§3.2) |

**Inference on a non-resident model** is submitted as an inference op with a *load dependency*: the arbiter first ensures residency (internally running a load op for the model — "load-on-admit", decision 3), then runs the inference payload. The user-visible queue position covers the combined wait.

### 2.5 VRAM accounting: reservations vs. residency

Unchanged principle (#185): one ledger, `VramReservationManager` (`tinyagentos/vram_reservation.py:59`), atomic check-and-reserve under a lock with the nvidia-smi probe run in a thread (`vram_reservation.py:117-165`). Reservations cover only the **in-flight load window**; once a model is resident, its VRAM shows up as *used* in the nvidia-smi probe, so no long-lived reservation is held — no double-counting. The ResidencyManager (§3) tracks per-model VRAM footprints from backend telemetry (`size_vram` in `/api/ps`, surfaced at `models.py:810-819`) so it knows how much an unload will free — it does not shadow the ledger.

---

## 3. Residency management

### 3.1 Resident-set model

New class `ResidencyManager` (new file `tinyagentos/scheduler/gpu_residency.py`), owned by the arbiter:

```python
@dataclass
class ResidentModel:
    model: str                  # backend model name
    backend_name: str
    backend_type: str           # "ollama" | "rkllama" | ...
    vram_mb: int                # from /api/ps size_vram, fallback to estimate
    loaded_at: float
    last_active_at: float       # updated when the model's task count drops to 0
    active_ops: int             # queued + running arbiter ops referencing it
```

- **ACTIVE** ⇔ `active_ops > 0` (derived purely from arbiter queue/running state — decision 3; there is no separate in-flight signal to maintain, and none exists today: `BackendCatalog` has no in-flight counter, which is why `LifecycleManager._wait_for_drain` falls back to a `getattr(..., lambda n: 0)` stub at `tinyagentos/lifecycle_manager.py:175-181`).
- **IDLE** ⇔ resident with `active_ops == 0`. `last_active_at` orders LRU.
- **Reconciliation:** a periodic sweep (piggybacking the existing 2 s queue-processor tick, `gpu_arbiter.py:536-543`) polls each local GPU backend's `/api/ps` (same call `loaded_models` makes, `models.py:805`) to (a) discover models loaded out-of-band, (b) learn true `size_vram`, and (c) notice backend-initiated expiry — ollama's own `keep_alive` timeout is visible as `expires_at` per model (`models.py:821`). The backend remains the source of truth for *what is resident*; the arbiter is the source of truth for *what is active*.

### 3.2 Load-on-admit and unload-idle-LRU

Admission for an op needing `required_vram_mb > 0` (a load, or inference requiring a load):

1. `_reserve_and_check` against the shared ledger (`gpu_arbiter.py:246-286`) — unchanged.
2. On **deny**, instead of today's immediate re-queue: ask the ResidencyManager for eviction candidates — **IDLE models only**, ordered by `last_active_at` ascending (LRU) — and unload them one at a time until `freed ≥ shortfall` or no candidates remain. Then retry the reservation.
3. If still denied, the op stays queued (position visible, §5) and the drain loop retries each tick.

**Never-evict-active is structural:** the candidate filter is `active_ops == 0`, and every GPU op — including every generation — is an arbiter op, so a model that is generating (or has queued work) is by definition not a candidate. The existing task-eviction path `evict_lowest_priority` (`gpu_arbiter.py:479-495`) remains for cancelling *tasks*; model-unload is the new, distinct mechanism that actually frees VRAM. Drain-before-unload needs no extra machinery: an IDLE model has, by definition, nothing to drain. Residual risk — an out-of-band caller hitting the backend directly, bypassing the gateway — is accepted and mitigated in §8.2.

### 3.3 The unload primitive (net-new)

Nothing in the codebase unloads a model today (verified: the only production `unload` references are doc comments and the unwired `core_aware_scheduler.mark_unloaded`, `tinyagentos/scheduler/core_aware_scheduler.py:177`; `CoreAwareScheduler` is referenced only from a docstring in `scheduler/scheduler.py:16-33` and is not instantiated in `app.py`).

New module `tinyagentos/backend_unload.py` (or a method set on the ResidencyManager):

- **ollama:** `POST {base}/api/generate` with `{"model": <name>, "keep_alive": 0}` and no prompt — the documented ollama idiom for immediate unload (the same `keep_alive` mechanism whose expiry timestamp we already read as `expires_at` from `/api/ps` at `models.py:821`). `/api/chat` with empty `messages` + `keep_alive: 0` is the chat-endpoint equivalent.
- **rkllama:** taOS treats rkllama as ollama-compatible (`providers/__init__.py:66`, and `/api/ps`, `/api/pull`, `/api/tags` are already used against it — `models.py:804-805`, `tinyagentos/installers/rkllama_installer.py:294`). **RESOLVED (verified live on the Pi, 2026-07-17, open question §10.2 closed):** the deployed rkllama fork exposes **explicit unload routes** — `POST /unload_model` and `POST /unload_models` (`src/rkllama/server/server.py:368,384`) plus `GET /api/ps` — and native memory-pressure eviction: `stop_worker()`, `unload_oldest_models_from_memory(memory_required)`, `unload_all_rknn_models_from_memory()`, and `expires_at`-driven `unload_expired_models()` (`src/rkllama/api/worker.py:839,993,1016,1039,1142`), with `keep_alive` also accepted at `server_utils.py:1082`. So rkllama models ARE eviction-capable. The unload primitive should call the explicit **`POST /unload_model`** route (cleaner and more deterministic than `keep_alive: 0`) rather than the ollama idiom. Bonus: rkllama already has its own idle/pressure eviction (`unload_oldest_models_from_memory`, `max_minutes_loaded_in_memory`), so the ResidencyManager should set that TTL generously and drive unloads explicitly so the arbiter — not rkllama's internal timer — owns the LRU decision (same principle as the ollama `keep_alive` note above). `unload_capable=False` is therefore NOT needed for rkllama.
- **llama-cpp / vllm:** single-model servers; no per-model unload. Phase 1: `unload_capable=False`. Process-level stop via the existing `LifecycleManager.drain_and_stop` (`lifecycle_manager.py`) is a follow-up integration, not Phase 1.

Unload is verified: after issuing it, the sweep re-polls `/api/ps` and only then reports the VRAM as reclaimable. The registered unload mechanism is invoked exclusively from the arbiter's admission/eviction path — no other code should call it.

### 3.4 Relationship to existing lifecycle keep-alive

`LifecycleManager` (`lifecycle_manager.py:25`) starts/stops whole backend **services** on idle timers; `keep_alive_minutes` is per-backend (`tinyagentos/config.py:411-421`). The ResidencyManager operates one level finer (per **model** within a running service). They compose: model-level idle-unload frees VRAM without stopping the service; service-level stop remains the lifecycle manager's job. The queue calls `lifecycle.notify_task_complete(backend_name)` (`lifecycle_manager.py:130-146`) on op completion so the existing service idle timers keep working. Additionally, taOS should set the backends' own idle unload generously long (ollama `keep_alive`) so the ResidencyManager — not the backend's 5-minute default — decides eviction; a backend-side expiry is tolerated (the sweep notices) but makes LRU decisions less deliberate.

---

## 4. Admission and concurrency

### 4.1 Per-model concurrency

Backends serialize or narrowly parallelize per model themselves (ollama queues internally; parallelism is backend-configured). The arbiter adds an explicit per-model limit so queueing happens where position is visible, not invisibly inside the backend:

- `max_concurrent_per_model` (default **1**, config key under the backend entry) — the arbiter runs at most N inference ops per model concurrently; further ops queue with a position.
- Multiple **different** models run concurrently when resident (multi-model residency is the point of the residency manager).
- Loads are additionally serialized **globally per backend** (one load at a time), preserving the arbiter's existing one-admission-per-drain-tick discipline (`gpu_arbiter.py:545-556`) and the Xid-62 rationale it encodes.

### 4.2 The uncontended fast path (decision 5)

For an inference op on a **resident** model with a free per-model slot:

1. Dict lookup: model in resident set → yes. (O(1), in-process.)
2. Per-model semaphore acquire without waiting → success.
3. `required_vram_mb == 0` → `VramReservationManager.reserve` returns immediately **without acquiring the lock or probing** (`vram_reservation.py:109-115`), and `_reserve_and_check` short-circuits the same way (`gpu_arbiter.py:259-260`). **No nvidia-smi subprocess on this path.**
4. Mark active, run payload (gateway streams bytes), emit trace on completion.

Measured budget: steps 1-4 are in-process operations — target **< 1 ms added latency**, enforced by a perf test (§9). The unavoidable extra cost for LiteLLM-routed traffic is the localhost proxy hop (one extra TCP/HTTP leg, streaming passthrough, no body buffering); expected low-single-digit ms on loopback, dwarfed by model latency. **Crucially, the fast path must NOT go through the 2 s drain tick** (`gpu_arbiter.py:539`) — `submit_gpu` already runs admitted work inline (`gpu_arbiter.py:361`); only denied/contended ops enter the ticked queue. A queued op's first admission opportunity is therefore ≤ 2 s after capacity frees; acceptable for the *contended* case only. (If review disagrees, an event-driven wakeup on release is a small extension — noted in §10.)

### 4.3 Contended path

- Inference on a non-resident model → enqueue (position visible), residency ensured via load-on-admit + idle-LRU unload (§3.2), then run.
- Inference on a resident model with all per-model slots busy → enqueue with position; admitted on slot release (FIFO within priority).
- Load with insufficient VRAM → enqueue with position; idle-LRU unload attempted each drain tick.
- Queue full (`max_queue_size=100`, `app.py:1189`) → today's `NoResourceAvailableError` (`gpu_arbiter.py:337-342`) maps to HTTP 503 at the gateway/routes — the *only* remaining 503, now meaning "queue overflow", not "VRAM busy".

### 4.4 Fail-open hosts (no NVIDIA probe)

On AMD/Apple/Rockchip (or failed nvidia-smi), the probe returns `None` and `reserve` **fails open with bookkeeping only** (`vram_reservation.py:126-136`, `_probe_vram` at `vram_reservation.py:272-292`); `available_vram()` returns `(0, 0)` (`vram_reservation.py:211-225`). Decision for the queue: **admission is unbounded** (nothing ever queues on VRAM grounds — matching today's fail-open convention), but the queue still provides value:

- per-model concurrency limits still apply (they don't need a probe);
- loads are still serialized per backend (concurrent-load protection);
- every op is still instrumented (decision 2);
- residency is still tracked from `/api/ps`, but **no VRAM-pressure eviction ever fires** (there is no shortfall signal). Backend-native idle expiry (ollama `keep_alive`) remains the only reclaim path on these hosts.

Rockchip note: rkllama VRAM/NPU memory is not probed today; this lands in the same fail-open bucket. A platform probe for RK3588 is future work (§10).

---

## 5. Queue position and UX

### 5.1 Position computation

Position of op X = 1 + count of queued entries ordered before X by the queue's ordering key `(priority, seq)` (`gpu_arbiter.py:48-57`), scoped appropriately:

- **Global position** for loads (they contend on VRAM + the per-backend load slot).
- **Per-model position** for inference ops (they contend on the model's slots) — "position 2" must mean "2 requests ahead of you *on this model*", or it misleads.

Implementation: the arbiter keeps a shadow `dict[task_id, _QueuedGpuTask]` alongside the `asyncio.PriorityQueue` (`gpu_arbiter.py:94`) so position reads are non-destructive — the existing `queue_snapshot()` drains and re-inserts the live queue to read it (`gpu_arbiter.py:634-650`), which is unfit for a hot polling endpoint and silently drops entries when the queue is full during re-insert (`gpu_arbiter.py:647-649`). The shadow dict is maintained at enqueue/dequeue/cancel; `queue_snapshot()` is reimplemented on top of it.

### 5.2 API surface (new)

- `GET /api/gpu/queue` — snapshot: `{queue: [{task_id, op, model, backend_name, submitter, priority, required_vram_mb, queued_seconds, position}], running: [...], residents: [{model, backend_name, vram_mb, state, last_active_at, expires_at}], stats: {...}}`. Extends the read-only pattern of `routes/scheduler.py:10-33`. `stats` includes the existing arbiter counters (`gpu_arbiter.py:612-623`).
- `DELETE /api/gpu/queue/{task_id}` — cancel (semantics in §5.5).

### 5.3 Delivery: SSE, not a new channel

The desktop already holds ONE persistent SSE connection to `GET /api/events/stream` (`routes/event_stream.py:36`; client at `desktop/src/hooks/use-event-stream.ts:69`) fed by the `EventBus` (`tinyagentos/events/bus.py:32`, `SystemEvent{kind, source, targets, payload}` at `bus.py:16-23`, `emit_event` helper at `bus.py:181-197`). The a2a bus stream (`routes/a2a_bus.py:146`) is a proxy to the external Pi bus — not used here.

New event kinds (emitted by the arbiter via `emit_event`, `source="gpu-queue"`, `targets=["user"]`, `level="info"`):

- `gpu.queue.update` — on any queue transition (enqueue, admit, complete, cancel, position change). Payload: the affected entries `[{task_id, op, model, submitter, position}]` plus `queue_depth`. Coalesced to ≥ 250 ms between emissions to avoid SSE spam under churn.
- `gpu.residency.update` — on load/unload/evict. Payload: `{model, backend_name, action: loaded|unloaded|evicted, vram_mb, free_vram_mb}`.

Note: `EventBus.emit` persists every event to the system trace store and derives user notifications for `warning|error` levels (`bus.py:108-152`); queue chatter stays at `info` and *does* include `"user"` in targets (that is what routes it to the SSE user channel) — the notification branch keys off `"user" in targets` too (`bus.py:124-133`), so `gpu.queue.update` events must be marked to skip notification derivation. Concretely: add a `notify=False` passthrough on `emit_event` or emit these on a dedicated non-notifying path — small EventBus extension, called out to reviewers.

### 5.4 UI states

**Models app** — `DownloadState` (`desktop/src/apps/ModelsApp.tsx:58-63`) gains a state:

```ts
interface DownloadState {
  downloadId?: string;
  percent: number;
  status: "starting" | "queued" | "downloading" | "complete" | "error";
  error?: string;
  queuePosition?: number;   // set while status === "queued"
}
```

- `POST /api/models/download` / `POST /api/models/pull` respond `{status: "queued", download_id, position}` instead of 503 when VRAM is short (§7). `handleDownload` (`ModelsApp.tsx:474-533`) maps that to `status: "queued"`.
- The existing poll loop (`GET /api/models/downloads/{id}`, `ModelsApp.tsx:149-196`) is reused: `DownloadManager` task status gains a `queued` status + `queue_position` field surfaced through `_task_to_dict` (`models.py:516-531`), so no second polling mechanism is needed; the SSE `gpu.queue.update` event additionally nudges position refreshes between polls.
- Rendering: the `DownloadProgress` card (`ModelsApp.tsx:114-236`) shows "Queued behind N" with an indeterminate bar; the 503 error card path (`ModelsApp.tsx:209-219`, fed from `data.error` at `ModelsApp.tsx:501-513`) stops being the VRAM-shortage UX and remains for genuine failures + queue overflow.

**Chat UI** — a reply message renders `state: "pending"` as a bare "..." until the first token (`desktop/src/apps/MessagesApp.tsx:200`, indicator at `MessagesApp.tsx:2422-2424`). New behavior: while pending, if an SSE `gpu.queue.update` event contains an inference op whose `submitter` matches the awaited agent slug, render "waiting — position N" in the pending indicator; clear on `position ≤ 0`, admit, or first delta. Matching is by agent slug (best-effort; see §5.6). Delivery uses the desktop's single SSE connection and its by-kind dispatch table (`desktop/src/hooks/use-event-stream.ts:27-69`) — MessagesApp registers a handler for `gpu.queue.update`; the stall-watch module (`MessagesApp.stallWatch.ts`) is the natural place to fold "queued" into the waiting logic so a queued turn doesn't trip the stall warning.

### 5.5 Cancellation

`DELETE /api/gpu/queue/{task_id}`:

- **Queued op:** remove from queue + shadow dict, cancel the submitter's `_arbiter_future` (the same future `submit_gpu` awaits, `gpu_arbiter.py:350-358`), emit `gpu.queue.update`. Frees capacity trivially (nothing was reserved — reservation happens at admission, `gpu_arbiter.py:562`).
- **Running inference:** cancel the asyncio task via the existing `_evict_task` ordering (cancel → await unwind → release reservation, `gpu_arbiter.py:497-534`); the gateway closes the backend connection, which aborts generation server-side.
- **Running load:** cancel the coroutine AND clean the backend: close the streaming `/api/pull` connection, then issue the backend's delete for the partial model (ollama `DELETE /api/delete {"name": ...}`) followed by an unload call if it registered as loaded, then release the ledger reservation. This closes today's gap where cancelling a pull leaves the backend pull running and VRAM consumed (there is currently **no cancel at all**: `DownloadManager` exposes only `start_download`/`start_installer_task`, `tinyagentos/download_manager.py:61,97` — cancel is net-new API on it, threaded through to a new `DELETE /api/models/downloads/{id}`). Whether a disconnected ollama pull truly halts server-side must be verified during implementation (open question §10.3); the delete call is the backstop that reclaims disk/VRAM either way.

### 5.6 Attribution (who is waiting)

- Loads: attributed precisely (`download_id` = `{app_id}-{variant_id}`, `models.py:378`).
- Gateway inference: the backend request carries no agent identity (LiteLLM's per-key metadata is not forwarded to the backend). Phase 1 attribution: the generated LiteLLM config sets a static per-model `extra_headers` with the backend name, and the gateway records `submitter` from an `X-Taos-Submitter` header **when present**, else `model` only. Precise per-agent attribution options are an open question (§10.4) — position display degrades gracefully to model-level ("your model is queued — position N").

---

## 6. Audit and benchmark integration (decision 2)

### 6.1 Trace events

Every GPU op emits exactly one trace record on completion (plus one on eviction/cancel), via the existing hook: `AgentTraceStore.record(kind, **fields)` (`trace_store.py:264`), obtained from `TraceStoreRegistry.get(slug)` (`trace_store.py:528-560`, wired at `app.py:1058`). Records automatically flow to OTLP because the OTel emitter is injected into every store (`app.py:1061-1076`). Slug = the submitting agent when known, else the `_unknown_`-style sentinel convention (`litellm_callback.py:27`) — we use `_system_` for loads/unloads and unattributed ops.

New kind `"gpu_op"` added to `VALID_KINDS` (`trace_store.py:70-81`; unknown kinds raise, `trace_store.py:152-153`). Field mapping onto the existing envelope (`trace_store.py:142-176`): `model`, `backend_name`, `duration_ms` (run time) go in the first-class columns; the payload carries the queue-specific data:

```json
{
  "op": "inference | load | unload | evict",
  "outcome": "ok | error | cancelled | evicted",
  "wait_ms": 0,
  "queue_position_at_enqueue": 0,
  "queue_depth_at_admit": 0,
  "required_vram_mb": 0,
  "free_vram_mb_at_admit": 0,
  "reserved_vram_mb_at_admit": 0,
  "resident_models_at_admit": 3,
  "evictions_triggered": ["model-a"],
  "priority": 10,
  "submitter": "agent-slug | _system_"
}
```

`free_vram_mb_at_admit` comes from the `GpuAdmission` result already computed at admission (`gpu_arbiter.py:61-68`, populated at `gpu_arbiter.py:271-272`) — no extra probe. Note the trace does **not** duplicate token counts or cost: the LiteLLM callback already records the `llm_call` event with `tokens_in/out`, `cost_usd`, `duration_ms` for the same completion (`litellm_callback.py:193-229`); `gpu_op` adds the queue/VRAM dimension. Joining the two is by `(model, time window)` in Phase 1; a shared correlation id (gateway injects, callback echoes) is future work (§10.4).

### 6.2 Benchmark layer

The benchmark store is `BenchmarkStore` (`tinyagentos/benchmark/store.py:45`, `record()` at `store.py:63-110`, keyed by worker/capability/model/metric), fed today by the first-join worker suite (`benchmark/runner.py:36`, results POSTed to `routes/benchmarks.py:54`). Policy is "run once on first add, manual after".

The queue supplies **continuous per-op observations** — a different cadence from suite runs. Phase 1: per-op data lives in the trace store only (above); the queue does **not** write per-op rows into `BenchmarkStore` (that would flood a table designed for suite results). Instead, a lightweight aggregator (daily or on-demand) can roll trace `gpu_op` events up into `BenchmarkStore.record(...)` rows with `suite_name="gpu-queue-live"`, `worker_id="local"`, metrics `gpu.wait_ms.p50/p95`, `gpu.load_ms`, `gpu.ops_per_hour` — reusing the leaderboard read paths (`routes/benchmarks.py:108-126`). The aggregator is optional scope; the owner can defer it (§10.5).

---

## 7. Behavior change, migration, rollout

### 7.1 Contract changes

| Today | After |
|---|---|
| `POST /api/models/download` (rkllama variant): reserve → **503** on shortage (`models.py:408-423`) | enqueue load op → `{status: "queued", download_id, position}`; proceeds when admitted |
| `POST /api/models/pull` (ollama): reserve → **503** (`models.py:669-684`); blocking `await POST /api/pull` with `timeout=300` (`models.py:696-700`) | enqueue load op; the pull runs as the op payload under `DownloadManager` (`start_installer_task`, `download_manager.py:97`) so the HTTP request returns immediately with a `download_id` — also fixing the current 300 s request-blocking shape |
| Pull cancel: **impossible** (no API) | `DELETE /api/models/downloads/{id}` + `DELETE /api/gpu/queue/{task_id}` (§5.5) |
| Inference: LiteLLM → backend direct (`litellm_config.py:261`) | LiteLLM → gateway → backend (§2.2) |
| Eviction: cancels asyncio task, frees nothing | idle-LRU model unload frees real VRAM (§3) |
| 503 meaning | only "queue full" (`max_queue_size` overflow) |

### 7.2 Routes / modules touched

- `tinyagentos/scheduler/gpu_arbiter.py` — op kinds, residency hooks, shadow queue dict, aging, trace/SSE emission.
- `tinyagentos/scheduler/gpu_residency.py` — new.
- `tinyagentos/backend_unload.py` — new.
- `tinyagentos/routes/gpu_gateway.py` — new; `tinyagentos/routes/models.py` — both pull sites; `tinyagentos/download_manager.py` — cancel + queued status.
- `tinyagentos/litellm_config.py` — api_base rewrite behind the flag.
- `tinyagentos/trace_store.py` — `VALID_KINDS += {"gpu_op"}`.
- `tinyagentos/events/bus.py` — non-notifying emit option (§5.3).
- `desktop/src/apps/ModelsApp.tsx`, `desktop/src/apps/MessagesApp.tsx` (+ `MessagesApp.stallWatch.ts`), `desktop/src/hooks/use-event-stream.ts` dispatch table.

### 7.3 Tests that change

- `tests` covering the 503 contract on `POST /api/models/pull` / `download` (asserting 503 on reservation denial) flip to asserting `queued`.
- `ModelsApp.download.test.tsx` / `ModelsApp.test.tsx` — new `queued` state.
- GPU arbiter tests extend, not break: new args are keyword-optional (§2.4).

### 7.4 Rollout: feature flag, three phases

Config key `gpu_queue.mode` (env `TAOS_GPU_QUEUE`): `off` | `shadow` | `on`.

- **Phase A — loads (`shadow`→`on`):** pull sites submit through the arbiter; `shadow` still 503s but records what *would* have queued (trace only). This alone closes #1864 as originally filed. No LiteLLM config change; zero inference risk.
- **Phase B — residency + unload:** unload primitive verified per backend (ollama first, rkllama on the Pi); idle-LRU eviction enabled. Existing-DB upgrade test discipline applies (schema untouched — no migrations — but the LiteLLM config regeneration path must be exercised over an existing install).
- **Phase C — inference via gateway:** LiteLLM `api_base` rewrite behind the flag; `shadow` mode = gateway forwards + instruments but never queues (pure passthrough, validating the latency budget in production before gating is enabled). Rollback = flip flag, regenerate LiteLLM config (`LLMProxy.reload_config`, `llm_proxy.py:532`).

Fail-open hosts run the same phases; Phase B is a no-op for eviction there (§4.4).

---

## 8. Error handling and risks

### 8.1 Biggest risk: the gateway sits on the inference hot path

A controller bug/restart now interrupts inference for all agents. Mitigations: `shadow` mode soak (Phase C); the gateway handler is deliberately dumb (parse model, admit, stream); on arbiter failure the gateway **fails open to passthrough** (admission errors must never turn into 500s for inference — log + trace and forward). Note `app.py` already tolerates arbiter startup failure by setting `app.state.gpu_arbiter = None` (`app.py:1194-1196`); the gateway treats that as passthrough mode. Controller restarts already restart LiteLLM (same supervisor); the added coupling is marginal but real — flagged for owner sign-off (§10.1).

### 8.2 Unload mid-generation

Structurally prevented for queue-routed work (§3.2). Out-of-band backend callers (a user curling ollama directly) could be generating on a model the queue sees as IDLE. Mitigations: eviction requires the model to have been IDLE for a minimum grace period (default 30 s) *and* — where the backend reports it — no active request; and taOS's port-hygiene posture already discourages direct backend access. Accepted residual risk; the failure mode is a killed out-of-band generation, not corruption (ollama unload of a busy model defers/interrupts at the backend's discretion).

### 8.3 Starvation / no aging

Ordering is strictly `(priority, seq)` (`gpu_arbiter.py:48-57`) with no aging: a stream of `INTERACTIVE_USER` inference can starve a `BACKGROUND` load forever. Fix (in scope): on each drain tick, any entry with `queued_seconds > aging_after` (default 60 s) is re-enqueued one priority class higher (numerically −10, floor at `INTERACTIVE_USER`), using the entry's existing `queued_at` (`gpu_arbiter.py:57`). Bounded, simple, testable.

### 8.4 Cancel mid-download

§5.5: connection close + backend delete + reservation release, in that order, all idempotent (`_release_reservation` is already idempotent, `gpu_arbiter.py:234-244`; `VramReservationManager.release` too, `vram_reservation.py:167-183`). The TTL sweep (`vram_reservation.py:244-270`, 1 h default `vram_reservation.py:46`) remains the backstop for leaked reservations.

### 8.5 Hot-path latency budget

- In-process admission on the resident fast path: **< 1 ms** (no lock contention: the zero-VRAM reserve path takes no lock, `vram_reservation.py:109-115`; the per-model semaphore is a plain asyncio primitive).
- Gateway hop (Phase C only): loopback HTTP, streamed; target **< 5 ms** added TTFB, validated in shadow mode against real traffic before enabling gating.
- Never on the fast path: nvidia-smi (subprocess, ~tens of ms) — only load-path admissions probe (`gpu_arbiter.py:263-268`).
- SSE emission is fire-and-forget and coalesced (§5.3); trace writes are post-completion, off the request's critical path.

### 8.6 Queue-position honesty

Position is advisory: eviction success, VRAM freed by external processes, and priority aging all reorder. The UI copy says "position N", never an ETA, and updates on every `gpu.queue.update`.

### 8.7 Backend restarts

If a backend restarts, `/api/ps` reconciliation (§3.1) empties the resident set for it; running ops fail and surface as op errors (traced with `outcome: "error"`); queued ops stay queued and admit against the fresh (empty) GPU.

## 9. Testing strategy

1. **Unit — residency:** ACTIVE/IDLE derivation from queue state; LRU candidate ordering; never-evict-active (an active model must never be selected even under maximal pressure); grace period; reconciliation against a fake `/api/ps`.
2. **Unit — queue:** position computation (global for loads, per-model for inference); shadow-dict consistency under enqueue/admit/cancel races; aging promotion; queue-full behavior; fail-open (probe `None`) admits everything and never evicts.
3. **Unit — unload adapters:** ollama `keep_alive:0` request shape; `unload_capable=False` backends are never candidates.
4. **Integration (fake backend httpx mock):** two concurrent pulls exceeding VRAM → second queues (not 503), admits after first completes; pull cancel issues delete + releases reservation; inference on non-resident model triggers load-then-run; per-model concurrency serializes as configured; gateway streams SSE-chunked backend responses byte-for-byte.
5. **Hot-path perf test:** uncontended resident-model admission overhead measured < 1 ms (in-process, CI-safe); assert zero subprocess spawns on that path (patch the probe and assert not-called).
6. **Contract tests:** updated 503 tests (§7.3); `GET /api/gpu/queue` shape; SSE event kinds registered in the desktop dispatch table.
7. **UI tests:** ModelsApp `queued` card with position; MessagesApp pending → "waiting — position N" → clears on delta (extend `MessagesApp.stallWatch.test.ts` so queued state does not trip the stall warning).
8. **Existing-DB upgrade test:** regenerate LiteLLM config over a pre-change install; verify agents keep completing in `off` and `shadow` modes (per the standing upgrade-test policy).
9. **On-hardware verification (Pi + Fedora/RTX 3060):** rkllama `keep_alive:0` behavior; real eviction under VRAM pressure with two models; Xid-62 non-regression (concurrent load attempt while eviction in progress).

## 10a. Decisions (owner-approved 2026-07-17 — build the proper feature, no shortcuts)

The owner approved the design and directed: pick the best long-term option for every open question, no quick fixes. Locked:

1. **Gateway coupling: ACCEPTED, with a permanently fail-open gateway.** The controller gateway is the single GPU front door (Phase C). It is NOT a hard dependency: the gateway is deliberately dumb and, whenever the arbiter is unavailable or exceeds a tight budget, it **transparently passes the request straight through to the backend** (the pre-gateway behavior). Shadow-mode soak is the rollout gate, and fail-open is a permanent resilience property, not a temporary crutch. No separate LiteLLM direct-fallback model entry is needed because the gateway itself degrades to passthrough.
2. **rkllama unload: RESOLVED** (§3.3) — explicit `POST /unload_model`; the arbiter owns LRU (set the backend TTL generously and drive unloads explicitly).
3. **Pull cancel: build real end-to-end cancellation.** Add a first-class `DownloadManager` cancel API that stops the in-flight pull and reconciles state; the `DELETE /api/delete` reclaim is a backstop, not the mechanism.
4. **Per-agent attribution: correlation header through LiteLLM.** Thread a request-correlation id from the gateway through LiteLLM so queue position is precise per-agent AND `gpu_op` traces join to `llm_call` traces (§6.1).
5. **Benchmark rollup: ship the trace→BenchmarkStore aggregator in this feature.** The single-front-door telemetry is the point; the aggregator ships, not deferred.
6. **User-pull priority: `INTERACTIVE_AGENT` (20).** Above background agent work, below an active user chat.
7. **Contended-admission latency: event-driven wakeup in Phase A.** A release immediately wakes the drain; no reliance on the 2s poll tick for the contended path.
8. **RK3588 memory probe: in scope.** Add an NPU/system-memory probe for Rockchip (rkllama already accounts memory for `unload_oldest_models_from_memory`), so VRAM-pressure eviction actually fires on the Pi (the primary box) rather than fail-open-never-evict. Other §10 "future" items (per-worker cluster queues, image-gen through the gateway) remain follow-ups.

## 10. Open questions for the owner (RESOLVED - see §10a)

1. **Gateway on the hot path — accept the coupling?** Phase C makes the controller a dependency of every agent completion (§8.1). Shadow-mode soak is the proposed gate; is that sufficient, or should Phase C additionally keep a LiteLLM fallback model-list entry pointing directly at the backend (fallback routing on gateway failure)?
2. **rkllama unload semantics — RESOLVED (live Pi check, 2026-07-17).** The deployed fork exposes explicit `POST /unload_model` + `/unload_models` routes plus native memory-pressure/`expires_at` eviction (`unload_oldest_models_from_memory`, `stop_worker`, `unload_expired_models` — `src/rkllama/api/worker.py`), and accepts `keep_alive`. rkllama models ARE evictable; use the explicit `/unload_model` route (§3.3). No owner decision needed. Follow-on choice (implementation detail, not blocking): drive unloads explicitly with a long backend TTL so the arbiter owns LRU, rather than deferring to rkllama's internal `max_minutes_loaded_in_memory` timer.
3. **Ollama pull-cancel behavior:** confirm whether closing the `/api/pull` stream halts the server-side download; the `DELETE /api/delete` backstop reclaims space either way, but the answer decides whether "cancel" also saves bandwidth.
4. **Per-agent attribution for gateway inference** (§5.6): is model-level position display acceptable for v1, or should we invest in a correlation header through LiteLLM (also enabling `gpu_op` ↔ `llm_call` trace joins, §6.1)?
5. **Benchmark rollup** (§6.2): ship the trace→BenchmarkStore aggregator in this feature, or defer (trace data is captured either way, so nothing is lost by deferring)?
6. **Priority for user pulls vs. agent inference:** a user-initiated model pull defaults to `BACKGROUND` (30) and will wait behind all inference until aging promotes it (§8.3). Alternative: user pulls start at `INTERACTIVE_AGENT` (20). Which matches intent?
7. **Contended-admission latency:** is the ≤ 2 s drain-tick admission delay acceptable for the contended case, or should the event-driven wakeup (release → immediate drain) ship in Phase A?
8. **Future:** per-worker queues on cluster nodes (the arbiter's cluster admission is capacity-only today, `gpu_arbiter.py:363-392`); image-gen backends through the gateway; RK3588 memory probe.
