# Resource Scheduler

**Status:** Phase 1 implemented — see `tinyagentos/scheduler/` and the closed tracking issue #25. Phase 2 (cluster dispatch + hot model swap) is tracked by #26. Phase 3 (aged queues + batching + per-agent quotas) is planned but unscheduled.

Unified queue for NPU / GPU / CPU-heavy work across the platform. Prevents
contention, exposes a clean priority/preemption model, and gives apps a
sensible place to ask for compute instead of fighting for it.

## Why this exists

Right now, every subsystem that wants inference just grabs whatever device
is free. rkllama preloads three models on the RK3588 NPU. Scrypted's RKNN
plugin wakes up whenever a camera detects motion. Image generation wants the
same NPU for ~10 seconds at a time. Embedding workers want it every few
hundred milliseconds. There is no arbiter.

The symptoms we've already hit:

- **Version-mismatched runtimes crash silently.** The RK3588 SD UNet was
  compiled with rknn-toolkit 2.3.0 and segfaults under librknnrt 2.3.2.
  Nothing in the current platform knew to flag or prevent this. The scheduler
  should refuse to dispatch a task to a resource whose runtime doesn't match
  the model's compile-time signature.
- **Image gen used to crash under rkllama contention.** Resolved by matching
  the runtime to the model, but the underlying lesson stands: multiple
  consumers on the NPU fight unless something arbitrates.
- **No backpressure.** If 20 agents each ask for an embedding at once, the
  backend gets flooded and they all time out.
- **No priority.** An agent's interactive reply waits behind a batch re-index.
- **No preemption.** You can't cancel a long-running generation to let an
  urgent chat message through.
- **No CPU/NPU choice.** Image gen today either always uses NPU (and crashes
  on contention) or always uses CPU (and is slow). The right call depends on
  what else is running right now.
- **Chat-while-generating is fragile.** A user chats with an agent. The agent
  wants to look up a memory (embedding + rerank, ~500 ms on NPU). Image gen
  is already using the NPU for the next 34 s. Without a scheduler, the
  embedding request either blocks, fails, or corrupts the NPU state. With a
  scheduler, it transparently reroutes to the CPU embedding backend and
  returns in under a second.

## Goals

- **One queue, one scheduler** for each physical resource (NPU, per-GPU, CPU
  inference pool). Callers submit a task; the scheduler picks when and where
  it runs.
- **Priorities** with sensible defaults: user-interactive > agent-interactive
  > background > batch.
- **Fallback routing** — if the preferred device is busy, and the task
  supports it, run on the next-best device. Image gen: NPU → CPU; embeddings:
  NPU → GPU → CPU.
- **Memory-aware admission** — don't start a 6 GB model load if RAM headroom
  is below some threshold; queue it instead.
- **Cluster-aware (phase 2)** — if the local NPU is saturated but a remote
  worker has a matching backend, route the task over the network.
- **Observable** — every queued/running/completed task shows up in the
  Activity app so you can see what's using what.

## Non-goals

- Strict real-time guarantees. This is best-effort — we're not scheduling a
  car.
- Replacing the backend HTTP clients. The scheduler sits in front of them,
  wrapping calls, not rewriting them.
- Per-process resource limits. cgroups already does that; we don't duplicate.

## Model

### Backend-driven discovery (load-bearing principle)

**The system's source of truth for "what can I run right now?" is the live
state of the backends, not the filesystem, not the manifest catalog, not a
config file.** Every level of the stack that asks "is model X available?"
gets its answer by talking to whichever backend would actually run it.

Why this matters, concretely:

- **Model catalog** — `/api/models` already joined the filesystem scan of
  `data/models/*.gguf` with the manifest catalog, then guessed at filenames
  to declare "downloaded". This broke in obvious ways (filename convention
  mismatch — see the dreamshaper rename incident) and in non-obvious ones
  (multi-file service installs like RKNN SD live outside `data/models/` and
  were invisible). The fix is to ask each backend "what models do you have
  loaded or immediately available?" and union those answers with the
  catalog manifest. Filename conventions become irrelevant.
- **Image generation** — `_get_image_backend` currently hardcodes a
  preference order by backend type (`sd-cpp → generic`). The
  correct question is "which backends report capability `image-generation`
  as healthy and have a compatible model loaded?" Answering that lets us
  add CUDA / Vulkan / Metal backends tomorrow with zero route changes.
- **Memory search / RAG** — same pattern: ask "who advertises
  `embedding` for the model I want?" rather than assuming rkllama is the
  one true embedding backend.
- **Scheduler admission** — a Resource's capability set is not declared in
  code, it's discovered from its backend's `/health` / `/v1/models` /
  `/sdapi/v1/sd-models` response. If the backend goes down or stops
  advertising a model, the scheduler stops routing to it automatically.

**What "ask the backend" looks like in practice:**

Every adapter in `backend_adapters.py` already implements `.health()` and
returns a list of `models` along with `status`. We extend that contract to
also return `capabilities` (what the backend can do) and `model_details`
(metadata about each loaded model — size, context length, quant, etc.).
The Scheduler periodically polls each registered backend, updates a
central `BackendCatalog` in-memory index, and every other subsystem —
`/api/models`, `_get_image_backend`, skill execution, capability checks —
reads from that index instead of their own private discovery paths.

**What the filesystem catalog is still for:** "what models could I
install?" vs "what can I run right now?". The on-disk catalog manifests
(`app-catalog/models/*.yaml`) describe the universe of known-good models
with verified download URLs. The runtime `BackendCatalog` describes the
intersection of the catalog with what's actually loaded in memory on which
backend right now. The UI shows both: "Available" (loaded, runnable now)
and "Installable" (in the catalog, not yet on this machine).

**Graceful degradation rule**: if a backend is unreachable or times out,
its entries are stale-marked but not immediately removed. The Scheduler
won't route new work to a stale backend but the UI still shows the models
as "previously available — reconnecting" instead of silently vanishing.

This principle applies to **everything** that asks "is X available?" —
models, capabilities, skills, backends, workers, GPUs, NPUs. The answer
is always: probe the live system and cache briefly. Never trust the
filesystem or config as the final word.

### Resources

A **Resource** is a thing that can run one task at a time (or N in parallel
when it supports it). On this Orange Pi today:

| Resource       | Concurrency | Owner                     | Notes                   |
|----------------|-------------|---------------------------|-------------------------|
| `npu-rk3588`   | 1           | rkllama / scrypted / sd   | 6 TOPS, 3 cores         |
| `cpu-inference`| 4           | sd.cpp / whisper.cpp      | 8 cores, budget 4 heavy |
| `gpu-mali`     | 1           | mesa-panthor (optional)   | rare, experimental      |

Cross-platform, the same abstraction covers every accelerator we plan to
support. Each is a separate Resource with its own queue and admission rules:

| Resource class  | Platforms                          | Typical runtime                     | Max concurrency             |
|-----------------|------------------------------------|-------------------------------------|-----------------------------|
| `npu-rk3588`    | Orange Pi 5 / Rock 5B              | librknnrt, rkllama, rknn-toolkit2   | 1 (or N via multi-context)  |
| `gpu-cuda-N`    | NVIDIA GTX/RTX/A/H                 | CUDA 11/12, cuDNN, TensorRT         | 1 per GPU (multi-GPU = N)   |
| `gpu-rocm-N`    | AMD RX 6000/7000, MI/Instinct      | ROCm 6.x, hipBLAS                   | 1 per GPU                   |
| `gpu-vulkan-N`  | Intel Arc, AMD RDNA, Apple, laptop | llama.cpp/sd.cpp Vulkan backend     | 1 per GPU                   |
| `gpu-sycl-N`    | Intel Arc, Intel iGPU              | oneAPI / SYCL                       | 1 per GPU                   |
| `gpu-metal`     | Apple Silicon M1-M5                | MLX, llama.cpp Metal, Core ML       | 1 (unified memory)          |
| `cpu-inference` | Everything                         | llama.cpp, sd.cpp, whisper.cpp CPU  | `min(physical_cores / 2, 8)`|
| `accel-hailo`   | Pi + Hailo-10H HAT                 | HailoRT                             | 1                           |

A **cluster Resource** (phase 2) wraps a remote worker's device via its
TinyAgentOS worker API — a GPU on a gaming PC, a Mac, an Android phone —
appearing in the local scheduler exactly like a local resource, just with
the added round-trip latency baked into the cost model.

### Platform and runtime signature

Each Resource carries a platform signature:

```python
@dataclass(frozen=True)
class ResourceSignature:
    platform: str           # "rk3588" | "cuda-sm_86" | "rocm-gfx1100" | "vulkan" | "metal" | "cpu-x86_64" | "cpu-aarch64"
    runtime: str            # "librknnrt" | "cuda" | "rocm" | "vulkan" | "mtl" | "none"
    runtime_version: str    # "2.3.0" | "12.4" | "6.0.2" | "1.3.280" | ...
```

A Task declares what signatures it requires:

```python
required_signatures: list[ResourceSignature] = [
    ResourceSignature(platform="rk3588", runtime="librknnrt", runtime_version="~=2.3.0"),
]
```

Admission walks the candidate resources and refuses any whose signature
doesn't satisfy the required constraint. This is how we prevent the 2.3.0
model / 2.3.2 runtime crash at the scheduler level: the task would literally
never be dispatched to an incompatible resource, it would fall through to
the next preferred device. The `~=` semver operator lets a task accept
"any 2.3.x runtime" when the model is compatible with minor version bumps.

Where runtime signatures live:

- **Backend manifests** — each app-catalog service manifest declares the
  runtime it provides (`provides_signature: {platform: rk3588, runtime: librknnrt, runtime_version: "2.3.0"}`).
- **Model manifests** — each model variant that targets an accelerator
  declares what runtime it was compiled against. For RKNN and TensorRT files
  this is mandatory; for GGUF/safetensors it's usually implicit ("any CPU").
- **Scheduler startup** — on boot, each registered Resource probes its
  actual runtime version by calling the backend's `/health` or equivalent,
  so manifest drift is caught early.

### Tasks

A Task has:
- `id` — UUID
- `capability` — `embed`, `rerank`, `image-generate`, `llm-chat`, `whisper`, etc.
- `preferred_resources` — ordered list: `[npu-rk3588, cpu-inference]`
- `priority` — `interactive-user`, `interactive-agent`, `background`, `batch`
- `estimated_seconds` — scheduler hint for admission
- `estimated_memory_mb` — for admission
- `submitter` — "images-app", "agent/alice", "skill:rerank", etc.
- `payload` — the actual call to make once admitted
- `cancel_token` — lets the caller bail out (or the scheduler preempt)

Tasks return via `asyncio.Future` — the call site awaits, the scheduler
decides when to actually do the work.

### Scheduler loop

For each Resource, a dedicated coroutine:

1. Wait on the resource's priority queue.
2. Pop the highest-priority task whose admission constraints (memory, model
   already loaded on this device, license allows) are satisfied.
3. If no candidates, sleep until something changes.
4. Mark the task `running`, call the payload, await.
5. On completion, release the resource and mark the task done.
6. On timeout, cancel the payload's cancel_token.

### Priority rules

- Higher priority preempts only at **task boundaries**. A running inference
  is never killed mid-flight; we just refuse to start new ones until the
  high-priority task clears.
- Within the same priority, FIFO.
- Aging: a background task gains +1 priority per 30 s waiting, capped one
  level below interactive. Prevents starvation.

### Admission rules

Before starting a task, check:

1. **Memory headroom** — `available_ram_mb >= estimated_memory_mb + 1024`.
   If not, queue.
2. **Runtime signature** — does the device's runtime match what the model
   was compiled against? If not, skip to the next preferred resource. A task
   will never get dispatched to a mismatched runtime.
3. **Model compatibility** — does this device have the right runtime for
   `capability` at all (e.g. an RKNN model cannot run on a CUDA GPU)? If not,
   route to the next preferred resource.
4. **Known incompatibility pairs** — a small allowlist of "don't run X on
   resource Y even if it otherwise fits" entries for edge cases we discover
   in the wild. Ideally empty, but the escape hatch is there.

### Fallback routing

The scheduler walks `preferred_resources` in order and picks the first one
that (a) can run the capability, (b) passes admission, (c) isn't at its
concurrency cap. If nothing fits, the task queues on its top preference.

The caller doesn't know or care which device actually ran the task — the
`capability` contract is stable.

### Worked example — chat-while-generating (the important one)

A concrete scenario to pin down the design. Alice types a question into the
chat with her agent. Meanwhile, image gen is running — NPU is occupied with
a 34 s LCM Dreamshaper pipeline that won't yield mid-batch.

Agent's chat handler needs to:

1. Embed the query (NPU preferred, ~100 ms)
2. Search vector memory (pure SQLite, not scheduled)
3. Rerank the top candidates (NPU preferred, ~400 ms)
4. Call the LLM with the retrieved context (NPU or cluster worker, seconds)
5. Stream the reply token-by-token back to Alice

Without the scheduler: the embedding call hits the NPU, collides with
image gen, something bad happens. Historically this segfaulted; with the
current matched runtime it probably works, but there's no guarantee and
the latency is unbounded.

With the scheduler:

```python
# agent's memory_search skill
query_vec = await scheduler.submit(
    capability="embed",
    preferred_resources=[
        ResourceRef("npu-rk3588", max_wait_ms=200),   # short fuse
        ResourceRef("cpu-inference"),                 # always available fallback
    ],
    priority="interactive-user",
    estimated_seconds=0.2,
    estimated_memory_mb=0,  # embedding model already loaded
    submitter=f"agent/{agent_id}:memory_search",
    payload=lambda: rkllama_or_cpu_embed(query),
)
```

What the scheduler does:

1. NPU is busy. Image gen has the lock for another ~30 s.
2. Alice's embed task has priority `interactive-user` and a `max_wait_ms`
   hint of 200 ms on `npu-rk3588`.
3. After 200 ms in the NPU queue, the scheduler gives up on NPU and tries
   the next preferred resource: `cpu-inference`.
4. `cpu-inference` has a CPU embedding backend ready (llama.cpp with a
   quantised bge-small or nomic-embed). Admission passes instantly.
5. CPU embedding returns in ~300 ms. Total wait for Alice: ~500 ms.

Key points:

- **Alice doesn't wait 34 s.** The scheduler recognises the NPU is busy
  and picks the best available alternative. Image gen is not interrupted,
  it finishes cleanly in the background.
- **The agent's code is unchanged.** It asks for the capability, not the
  device. The scheduler handles the "which device right now" decision.
- **No mid-task preemption needed.** Image gen iters are 5.66 s each and
  don't yield — preempting inside a C++ inference call is a minefield.
  Fallback routing sidesteps the problem entirely.
- **The user can also choose to wait.** A request can opt out of CPU
  fallback with `preferred_resources=[ResourceRef("npu-rk3588", max_wait_ms=None)]`
  — then the task blocks until the NPU is free. Useful for "I want the NPU
  speed and don't care about latency" batch work. Opt-in, not the default.
- **When image gen *does* finish**, the scheduler wakes up any waiters on
  `npu-rk3588` in priority order. An LLM chat completion that was happy
  to wait for the NPU gets dispatched immediately.

The same pattern works symmetrically: if Alice starts a chat and the NPU
is idle, her embedding runs on NPU (fast). If she then asks for an image,
it waits for her chat embedding to clear the NPU (a few hundred ms) and
then runs.

**What if we need to guarantee the memory lookup happens?** The agent can
also use `submit_blocking()` which never falls back and always waits for
the primary resource. This is the "wait for image gen to finish, then check
memory" semantic. Default is the fallback-routing behaviour above because
it's almost always what you actually want; blocking is the escape hatch.

## Integration points

### Today (phase 1)

The `DownloadManager` already exists and queues downloads. We wrap it and
all the other consumers of inference backends:

```python
# before
resp = await http.post(backend_url + "/v1/embeddings", json=...)

# after
async def run():
    return await http.post(backend_url + "/v1/embeddings", json=...)

result = await scheduler.submit(
    capability="embed",
    preferred_resources=["npu-rk3588", "cpu-inference"],
    priority="interactive-agent",
    estimated_seconds=0.5,
    estimated_memory_mb=0,  # model already loaded
    submitter="skill:memory_search",
    payload=run,
)
```

Call sites that need to be wrapped (in rough order of importance):

1. `tinyagentos/routes/images.py` — `_get_image_backend` → use scheduler to
   pick NPU or CPU at request time, not statically.
2. `tinyagentos/tools/image_tool.py` — agent skill path, same wrapping.
3. `tinyagentos/qmd_client.py` — embeddings for user memory and agent memory.
4. `tinyagentos/routes/skill_exec.py` — all inference-heavy skills.
5. `tinyagentos/routes/agents_chat.py` — LLM chat calls.

### Tomorrow (phase 2)

- Cluster-aware dispatch: the scheduler is aware of remote workers and can
  route a task to one of their resources when the local is saturated.
- Batching: several small embed requests arriving within a 50 ms window get
  coalesced into one batch call.
- Per-agent quotas: `agent/alice` can only consume up to 40 % of the NPU.
  This is already the rough idea behind per-agent API keys in LiteLLM, so we
  pair it with scheduler-level enforcement.
- Pre-emptive warmup: if the scheduler sees a chat request coming (e.g.
  websocket typing indicator), it can pre-load the model in the background.

## Observability

The Activity app already shows CPU / NPU / memory. Add a fourth panel:

- **Active tasks** — table of running work with submitter, capability,
  device, elapsed time, est remaining
- **Queue depth** — bar chart per resource
- **Rejected / preempted** — counter + last error

Every scheduler event (enqueue, start, complete, reject, preempt) is also
recorded to the metrics store (`metrics.db`) for historical graphs.

## Failure handling

- **Task exceptions** propagate to the caller's Future; the scheduler logs
  them and records them in metrics. No retry at the scheduler level — the
  caller decides whether to retry with a different device.
- **Resource crashes** — if a backend dies mid-task, the scheduler marks it
  unhealthy, times out the task, and excludes the resource from new
  admissions for `BACKOFF_SECONDS` (default 30).
- **Scheduler itself crashes** — not expected but if it does, tasks on the
  queue are lost. Callers see `TaskCancelledError` and can decide what to do.
  The scheduler is not a durable queue; durable work goes to the existing
  task scheduler / cron subsystem.

## Cross-platform notes

The scheduler is designed for a single abstraction across RK3588, NVIDIA,
AMD, Intel, and Apple Silicon. Platform-specific quirks live inside the
Resource implementation, not in the scheduler core or the caller API.

### What's already portable

- **Backend adapters** (`backend_adapters.py`) — any backend that speaks
  HTTP with an OpenAI-compat, A1111-compat, Ollama-compat, or custom raw API
  is a one-class addition. No scheduler changes needed when we add CUDA
  sd-server, ComfyUI, Fooocus, mlx-lm, etc.
- **Hardware tiers in model manifests** — the catalog already distinguishes
  `x86-cuda-8gb`, `x86-vulkan-8gb`, `apple-silicon`, `arm-npu-16gb`, etc.
  The scheduler uses these to pick the right model variant for the target
  resource at task submission time.
- **stable-diffusion.cpp** — the same sd-server binary rebuilds with
  `-DSD_CUDA=ON` / `-DSD_VULKAN=ON` / `-DSD_METAL=ON` / `-DSD_SYCL=ON` and
  exposes the identical A1111 API. The `sd-cpp` backend adapter works as-is
  across all of them; only the install script changes per platform.
- **llama.cpp family** — same story. GGUF models plus the right build flags.
- **MLX** — Apple Silicon gets its own `mlx` backend type on Mac workers,
  registered with platform `mtl` in the resource signature.

### What's RK3588-specific today and will need abstracting

- `librknnrt.so` version matching and the `/usr/lib/librknnrt.so` install
  step. These become the Rockchip-branch of a broader "accelerator runtime
  install" pipeline that includes CUDA toolkit detection, ROCm version checks,
  Vulkan driver probing, and Metal framework availability.
- The current Images-route preference order (`sd-cpp → generic`) is
  hardcoded. Once the scheduler exists, this becomes `preferred_resources`
  on the task, and the selection order is generated from the host's hardware
  profile at boot: a gaming PC gets `[gpu-cuda-0, cpu-inference]`, a Mac gets
  `[gpu-metal, cpu-inference]`.
- Install scripts are per-platform: `install-comfyui-cuda.sh` /
  `install-sdcpp-vulkan.sh` for desktop/server, etc. The app catalog service
  manifest points to the right installer based on detected hardware.

### What changes per-platform for the runtime-signature check

The compile-time-to-runtime match we care about for RK3588 (librknnrt 2.3.0
vs 2.3.2) has analogues on every platform:

| Platform     | Common version-pinning pitfall                                               |
|--------------|------------------------------------------------------------------------------|
| NVIDIA CUDA  | Model built against cuDNN 9.x won't run on cuDNN 8.x; sm_XX compute capability mismatch |
| AMD ROCm     | MIopen version / gfx arch mismatch between compiled kernel and hipBLAS       |
| Intel Vulkan | SPIR-V module compiled for a newer driver crashes on older Mesa              |
| Apple Metal  | Core ML program requires min OS version; MLX version / Python binding drift  |
| Intel SYCL   | oneAPI major version / compiler-runtime mismatch                             |

In every case the fix is the same at scheduler level: the Resource exposes
its runtime signature, the Task declares its requirements, admission control
does the match, and fallback routing handles rejection. The platform-specific
bit is *how* we probe the version — `cudaRuntimeGetVersion()`, `hipRuntimeGetVersion()`,
`vkEnumerateInstanceVersion()`, etc. — not what the scheduler does with the
result.

## Phased rollout

### Phase 1 — local scheduler (MVP)
- Implement `tinyagentos/scheduler/` with `Resource`, `Task`, `Scheduler`
  classes.
- Auto-discover local resources from the hardware profile (`rkllama`
  running → `npu-rk3588` exists).
- Wrap image gen first — the immediate pain point. Route NPU when safe,
  CPU when busy. Ship with the rknn + sdcpp server pair behind it.
- Add a tiny "Active tasks" widget in the Activity app.

### Phase 2 — platform-wide adoption
- Wrap embeddings, rerank, and LLM calls.
- Add aging, admission control, batching.
- Per-agent quotas.
- Metrics history graphs.

### Phase 3 — cluster-aware
- Remote worker resources are first-class scheduler citizens.
- Task cost model: pick device by latency × load, not just preference order.
- Pre-emptive warmup.
- Cross-device model cache coherency ("Alice's chat is on node-2; route her
  next message there to reuse the loaded model").

## Open questions

- **Where does the scheduler live — in-process with the main FastAPI app, or
  as its own service?** MVP: in-process. Moves to its own service when phase
  3 needs cross-process coordination.
- **What heuristic decides NPU vs CPU for image gen?** Start simple: NPU if
  `npu-rk3588` has < 1 queued task AND rkllama is idle; otherwise CPU. Refine
  with real measurements.
- **How does this interact with the existing fallback manager for
  backends?** The fallback manager handles "backend A unhealthy, use B" at
  the HTTP level. The scheduler handles "device A busy, use B" at the
  capability level. They compose: scheduler picks the capability → fallback
  picks the healthy backend for that capability.
- **Do we need a config file for resources?** Initially: auto-discover.
  Later: `data/scheduler.yaml` lets users pin specific backends to specific
  resources.

## References

- Existing `DownloadManager` — same pattern, scaled up.
- Existing cluster manager — worker registration surface we build on.
- asyncio.PriorityQueue — the queue primitive.
