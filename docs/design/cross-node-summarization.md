# Cross-node conversation summarization

Status: design spike for taOS #156 (phase-3 / future). Doc only — no
implementation in this change. Slices S1–S3 below are the dispatchable plan;
section 7 is a pre-registered quality gate that decides whether S3 ships.

## 1. What it is

Long-running agents accumulate conversation history that grows without bound.
Today the live path answers that with **truncation**: `build_context_window()`
in `tinyagentos/chat/context_window.py` trims to a message count limit and a
token budget and **drops oldest messages first**. Nothing is summarised, so the
oldest context is simply lost from the model's view; the zero-loss archive
(taosmd `archive.py`, "never modified, never deleted, never summarised") still
holds it, but nothing puts a *compressed* version back in front of the model.

Cross-node summarization moves the compression work off the node doing the live
interaction. Segments older than a configurable threshold are compressed — once,
in the background — by **idle CPU-heavy nodes** that hold no GPU lease. The live
node keeps only the recent hot window; older segments exist as summary chunks
(text, plus an embedding) that the runtime can splice back in when a
conversation references something old.

Relationship to existing pieces:

- **The zero-loss archive is the raw truth, and stays that way.** Summaries are
  derivatives keyed back to the raw segment; they never replace or mutate it.
  Any summary can be discarded and rebuilt from the archive.
- **taosmd already ships the compression primitives, none of them clustered.**
  `SessionCatalog.enrich_session()` (`taosmd/session_catalog.py`) runs an LLM
  over a split session and writes topic / description / category;
  `taosmd/crystallize.py` compresses a completed session into a "crystal"
  (narrative + outcomes + lessons). The taosmd pipeline job queue
  (`tinyagentos/scheduling/job_queue.py`) serialises exactly this class of work
  (`JOB_ENRICH`, `JOB_CRYSTALLIZE`, `JOB_EMBED`, `JOB_SPLIT`) — and its own
  module docstring is explicit that it is **not** a distributed task system:
  *"this is a single-device queue for one taOS controller. For cluster-level job
  distribution, use taOS's worker dispatch."* Nor is its consumer wired up:
  `JobWorker` (`scheduling/job_worker.py`) is never instantiated by
  `app.py` today, and `app.state.job_queue` is created lazily by
  `routes/jobs.py` on first request. This design is the "cluster-level
  distribution" the docstring points at.
- **`#154` (conversation state handles) is the addressing layer.** This design
  consumes its vocabulary — `conversation_id`, `kv_summary_location`,
  `vector_shard_ids`, `active_worker_id` — and defines no handle of its own.
  Segments are named relative to a handle.
- **`#155` (RAG data locality policies) is the retrieval layer.** Fetching a
  summary chunk back is a normal RAG call routed by the collection's
  `locality_policy` (`local` / `preferred-node` / `nearest` / `broadcast`), not
  a bespoke cross-node fetch invented here.
- **`#897` (Cluster app capability map + placement) is the placement layer.**
  This design borrows its vocabulary — *node*, *capability map*, *placement
  suggestion* (`tinyagentos/cluster/capability_map.py`, `cluster/optimiser.py`,
  `routes/cluster_capability.py`) — and adds one predicate to it (section 3)
  rather than a parallel placement model.
- **GPU leases are the exclusivity signal.** `GpuLease`
  (`cluster/worker_protocol.py`), claimed through
  `POST /api/cluster/leases/claim` and tracked in `ClusterManager._leases`, is
  the hard "this node's accelerator is taken" fact that eligibility reads.
- **Agent ownership is the privacy boundary.** An agent belongs to a user:
  every row in `agent_registry_store` carries `user_id`, and
  `auth_context.resolve_agent_owner()` / `require_agent_owner_or_admin()`
  (`tinyagentos/auth_context.py`) are the house primitives that turn an agent id
  into its owner, fail-closed for non-admins. A summary is conversation content,
  so it is owner-scoped the same way — sections 4.3 and 4.4.

## 2. Architecture

```
   live node (agent runtime)                idle producer node (any eligible)
        |                                          ^
        | 1. hot window only                      | 3. run one bulk job
        v                                          |    (LLM: segment -> summary)
  chat_messages / archive (raw, append-only)       |
        |                                          |
        | 2. segment crosses age threshold         |
        +--> JobQueue.enqueue("summarize_segment", BACKGROUND)
                         |                         |
                         | 4. placement: capability map + eligibility
                         v                         v
                   ClusterManager / TaskRouter -----+
                         |
                         | 5. result payload -> controller commits
                         v
                  summary store (text + provenance)  +-> vector index (embedding)
                         |
        +----------------+----------------+
        |                                 |
  6. live node references an old topic     7. stale check: source_sha256
     -> RAG call (#155 policy)                matches current segment?
     -> splice marked summary block           no -> ignore, re-summarise
        ahead of the hot window
```

Two invariants the diagram encodes:

1. **The raw segment store is written by exactly one thing: the conversation
   itself.** Summarisation is a pure reader of it plus a writer of derived rows.
2. **The live inference path never waits on summarisation.** Enqueue is
   fire-and-forget; the only summarisation-related work the live node does
   synchronously is the splice lookup in S3, and that degrades to today's
   behaviour when it misses.

## 3. Which nodes are eligible, and how eligibility is determined

Eligibility is a **derived predicate evaluated at dispatch time**, never a
persisted boolean — every input is live worker state that changes on the next
heartbeat.

A node is eligible to run a summarization job when **all** of these hold:

| # | Condition | Source of truth |
|---|---|---|
| 1 | `worker.status == "online"` | `WorkerInfo.status`, set by `POST /api/cluster/heartbeat` |
| 2 | `worker.kind != "device"` | `WorkerInfo.kind` — a Bluetooth-paired taOSusb board is never a placement candidate for any job type |
| 3 | Not draining | `ClusterManager.drain_worker()` / `worker.status == "draining"` |
| 4 | Heartbeat fresh | `WorkerInfo.last_heartbeat` within the monitor loop's staleness window |
| 5 | **No active GPU lease on any of its resources** | `ClusterManager.get_leases()` matched on the `"{worker_name}:{resource_name}"` prefix of `GpuLease.resource_id` |
| 6 | Idle: `worker.load <= idle_load_ceiling` | `WorkerInfo.load`, the 0–1 utilization the worker self-reports |
| 7 | Can actually do the work: a chat-capable backend is resident **or** startable | `worker.capabilities` (backend-driven, `detect_capabilities()`), `WorkerInfo.available_models`, and the capability map's `potential_capabilities` via `cluster/capabilities.py` |
| 8 | Free memory for the summariser model | `worker.free_vram_mb` / `worker.used_vram_mb`; `None` means **unknown, not zero** (the existing contract — a CPU node has no VRAM probe and must not be permanently un-eligible) |

Notes that matter for correctness:

- **Condition 6 is a real gate, not a tiebreak.** A node above
  `idle_load_ceiling` is not eligible and the job stays pending — that is what
  S1 asserts. The load figure is *self-reported*, so the ceiling is set
  conservatively; it is not a precise instrument. Leases (condition 5) are the
  hard reservation signal, and the ranking between two already-eligible nodes is
  a separate concern from admission.
- **Lease absence is exactly as trustworthy as lease coverage — which is
  currently incomplete.** `ClusterManager.claim_lease` is the only path that
  creates a visible `GpuLease`, and not every GPU-capable inference path takes
  one. The mounted LLM gateway is the concrete counter-example: with
  `TAOS_LLM_GATEWAY=1`, `POST /api/llm/v1/chat/completions`
  (`tinyagentos/llm_gateway/forward.py`) forwards straight to the configured
  backend, and an Ollama backend on that route can be using the GPU with **no
  lease recorded**. Condition 5 must therefore not be treated as a proof of
  idleness until either (a) every GPU-capable inference path claims, renews and
  releases a visible lease, or (b) the predicate corroborates lease absence with
  the VRAM sample / load signal. This is a prerequisite for S2, not a detail —
  see open question 6.
- **Eligibility is per *node*, not per *resource*.** A CPU-only node reports
  `cpu-inference` in `WorkerInfo.resources` and holds no leases at all, which is
  exactly the "idle CPU-heavy node" the issue describes. The predicate does not
  special-case CPU: it says "this node is free", which CPU boxes trivially
  satisfy.
- **GPU nodes are eligible but not preferred.** A GPU box with no lease and no
  load can summarise; it just should not be the first pick, because that
  hardware is the scarce resource latency-class work wants. Ranking (not
  eligibility) encodes the preference — reuse the `Tier` ordering in
  `scheduler/resource.py` (`GPU=0 < NPU=1 < CPU=2 < CLUSTER=3` intentionally
  ranks *fastest*, so summarization needs its own inverted preference: cheapest
  first).
- **Job class.** Summarization is `Priority.BACKGROUND` in the existing
  `JobQueue` enum ("overnight maintenance, rebuilds"), and maps to the `bulk`
  class in the SLO-aware scheduler addendum (`docs/design/slo-aware-scheduler.md`):
  *"Hours to days, job queue semantics — Run only on idle cycles."* That
  addendum is not implemented (no `slo_class` in code today), so S1 records
  the class as a label on the job payload and does not require the scheduler
  work to land first.
- **Where this lands in the capability map.** #897 owns the node/capability
  view. This design adds one derived field to it — an eligibility reason string
  plus a boolean — so the Cluster app can *show why* a node is or is not in the
  summarization pool. It must not add a second placement store.

## 4. How compressed context is produced, stored, and pulled back

### 4.1 What a segment is

A **segment** is a contiguous run of messages in one conversation that is older
than `summarization.threshold_messages` / `summarization.threshold_age`, and
that is no longer inside the live hot window. The natural grouping already
exists in the memory pipeline: `SessionCatalog.split_day()` groups archived
events into sessions by time gap, so the summarizer reuses that split rather
than inventing a new segmentation. A segment is identified by
`(conversation_id, segment_id)` where `segment_id` comes from the
`conversation_id` + shard addressing defined by #154.

### 4.2 Producing a summary

One job type, `summarize_segment`, with a payload of the form:

```json
{
  "conversation_id": "...",
  "segment_id": "...",
  "agent_name": "...",
  "user_id": "owner-of-record",
  "source": {"first_message_id": "...", "last_message_id": "...", "sha256": "..."},
  "state_handle": { "kv_summary_location": "...", "vector_shard_ids": [...] },
  "model": "qwen3:4b",
  "target_tokens": 400
}
```

The producer is a normal LLM call — the same shape as today's
`SessionCatalog.enrich_session()` / crystallize path — run on the eligible node,
prompted to emit a compact summary plus structured items (topics, decisions,
open threads). `sha256` over the canonical raw segment text is carried in the
payload and stored on the result: it is the staleness key in section 5.

`user_id` is the **owner of record**, resolved by the controller when the job is
enqueued through the same agent→owner binding the house already uses
(`auth_context.resolve_agent_owner()`; for tool calls,
`tools/todo_tools.py::_resolve_owner_user_id()` — registry `get_by_handle`,
falling back to the authenticated `request.state.user_id`). The controller
**re-resolves the owner from `agent_name` at commit time** rather than trusting
what a producer echoes back: ownership is a controller-side authorization fact,
so a compromised or merely buggy producer must not be able to attribute a summary
to a different user.

**The controller commits; the producer does not.** A producer node never opens
the controller's summary store or archive. It returns a result payload and the
controller writes it. This is what keeps a compromised or merely buggy node from
being able to write conversation memory.

### 4.3 Storing it

One new store following the house SCHEMA/MIGRATIONS discipline
(`BaseStore`, `init()`/`close()`, attached to `app.state` in the lifespan):

```
summaries(
  user_id          TEXT,   -- owner of record (registry-resolved; see 4.2)
  conversation_id  TEXT,
  segment_id       TEXT,
  agent_name       TEXT,
  summary_text     TEXT,
  source_sha256    TEXT,   -- staleness key
  first_message_id TEXT,
  last_message_id  TEXT,
  model            TEXT,   -- which model produced it (quality attribution)
  produced_on      TEXT,   -- which worker name
  created_at       REAL,   -- UTC
  PRIMARY KEY (user_id, conversation_id, segment_id, source_sha256)
)
```

`user_id` leads the key because under per-user agent namespacing **neither
`agent_name` nor `conversation_id` is unique on its own**: two users can each own
an agent called `assistant`, and nothing in this schema would stop their segments
from colliding. Making the owner part of the row identity keeps summarisation
idempotent *for that owner* and stops one user's row from shadowing or
overwriting another's. It is the same shape as the existing user-scoped store in
the tree: `knowledge_store.py` carries `user_id TEXT NOT NULL DEFAULT ''`
(its migration v1 adds the column and `idx_ki_user_id`), and its reads take
`user_id: str | None = None` so a caller must supply an owner to get owner-scoped
results. A store that cannot resolve an owner returns nothing, not everything.

The primary key makes summarisation **idempotent by construction — provided
the insert carries an explicit conflict policy**: SQLite's default on a
constraint violation is `ABORT`, so the write is
`INSERT ... ON CONFLICT (user_id, conversation_id, segment_id, source_sha256) DO NOTHING`
(the house already uses this form — see the `ON CONFLICT(node_id) DO UPDATE`
upsert in `cluster/capability_map.py`). With that, re-running an unchanged
segment is a no-op *for that owner*, and a changed segment gets a new row while
the old one stops matching `source_sha256` and is ignored.

The summary row does **not** cover the vector index write — see the crash-recovery
row in section 5 for how the two are kept consistent.

The embedding goes to the existing vector index (`taosmd/vector_memory.py`
stores `text` + `embedding` + `metadata_json` in SQLite; the qmd serve index is
what `routes/memory.py` proxies per agent) with `metadata_json` carrying
`{user_id, conversation_id, segment_id, source_sha256, kind: "summary"}`.
Retrieval is therefore an ordinary RAG call, which is the whole point of the
#155 dependency — though the RAG index is not the only lookup (section 4.4).

The owner scopes the *write* as well as the row: the embedding lands in the
**owner's** memory index. `routes/memory.py::_agent_db_path()` turns the caller's
`agent` component into `agent_memory_dir/<agent>/index.sqlite` and rejects a
multi-segment value, so under per-user agent namespacing the component that is
passed must be the **owner-qualified** agent identity, never the bare
`agent_name`. A bare name resolves to whatever index that name points at, which
is exactly how one user's summaries would become retrievable by another.

### 4.4 Pulling it back

When the live node assembles context, it keeps the current behaviour for the hot
window and, for a reference that falls outside it:

1. resolve the summary with a **two-tier lookup**:
   - **preferred:** an ordinary memory/RAG query (the `routes/memory.py` proxy
     path, routed by the collection's #155 locality policy) **against the
     owner's index** and filtered to `kind: "summary"` for that conversation —
     this is the path that survives a summary-store/node relocation and is what
     #155 buys us;
   - **fallback:** a direct summary-store read keyed by
     `(user_id, conversation_id, segment_id, source_sha256)` on the controller —
     the **current** segment's hash, computed by the caller from the raw text,
     is part of the key. Keying on the hash rather than on
     `(conversation_id, segment_id)` alone matters: the store deliberately keeps
     one row per hash, so a segment-level lookup could return a superseded row,
     fail the step-2 check, and hide a perfectly good current summary for the
     whole retry window. With the hash in the key this is a single point read
     and the ambiguity cannot arise. This tier exists precisely because a
     committed summary can be **spliceable while not yet vector-searchable** (an
     index write still draining through the outbox in section 5); without it, a
     summary would be invisible for the whole retry window despite already being
     correct.
2. verify `source_sha256` against the current raw segment. For the RAG tier this
   is a real check — the query returns candidates by similarity and the row it
   hands back may be superseded. For the direct tier the hash is already part of
   the lookup key, so this step is a confirmation of the same value, not a
   second, different filter. Either way a stale row is rejected.
3. splice the summary text in **ahead of** the retained hot window as a
   distinctly marked block (segment time range + "summarised" marker) so the
   model — and, in a transcript view, the user — can tell derived text from
   verbatim text.

A summary becomes **spliceable at commit**, not at reconciliation: the two-tier
lookup is what makes that true rather than merely asserted.

Step 2 is what stops a stale summary from overruling live context: **the raw
segment and the hot window always win.**

Both tiers are **owner-scoped**. The owner comes from the live turn's
authenticated identity (`request.state.user_id`, set by the session or
local-token auth middleware) or, for an agent caller, from the agent→owner
binding of section 4.2 — never from a name the caller supplies. `agent_name`
alone is not a scope: under per-user agent namespacing it is not unique
(section 4.3), so an unscoped lookup for a colliding name/conversation pair can
hand back another user's summary. A lookup that cannot resolve an owner resolves
to **no** summary and takes the section 5 degraded path rather than degrading to
an unscoped read.

## 5. Failure and consistency semantics

| Failure | Semantics |
|---|---|
| **Stale summary vs live context** | Summaries are keyed on `source_sha256`. A summary whose key no longer matches the current raw segment is *ignored*, never merged. The raw text (hot window, then archive) is authoritative at all times; splices are additive. |
| **Duplicate / concurrent summarisation of one segment** | The composite primary key — owner-scoped, section 4.3 — plus an explicit `ON CONFLICT ... DO NOTHING` makes the second write a no-op rather than a constraint error. Two producers racing is wasteful, not corrupting. |
| **Cross-user retrieval** | Every row and every lookup carries the registry-resolved owner (`user_id`): the direct tier keys on it, the RAG tier queries the owner's index, and the vector metadata carries it. `agent_name` is never a scope on its own, because under per-user agent namespacing the same name can exist for two users. A lookup that cannot resolve an owner returns **no** summary and takes the degraded path below — it never degrades to an unscoped read. |
| **Summary row committed, index entry missing** | The vector index is **not** in the summary store's SQLite transaction: it is owned by the qmd serve process and called over HTTP (`tinyagentos/qmd_client.py`, proxied by `routes/memory.py`), so it cannot join a local commit. Committing the summary row and a durable **index-write outbox** row together, drained by a reconciler that retries an idempotent qmd upsert until it succeeds, is the recovery contract — the house already has this pattern in `tinyagentos/chat/peer_outbox.py` (attempt counter, `next_retry_at`, exponential backoff). A summary row whose index entry has not landed yet is still correct and still spliceable through the direct store lookup (section 4.4); it is only vector *discovery* that is delayed. |
| **Producer node dies mid-job** | The job is a `BACKGROUND` job claimed with an atomic pending→running UPDATE and **no heartbeat or claim TTL** (only GPU leases and BLE pairing carry TTLs today), so a producer that dies mid-job leaves its row `running` until the controller's startup sweep (`JobQueue._sync_init`) marks it `failed` on the next restart and a later pass re-enqueues it from the idempotent source. No partial writes are possible because the producer never writes controller state at all — the controller's only write is the completed-result commit above. A mid-job claim-TTL/reaper (recovery without a restart) is explicitly S2 scope. |
| **Controller restarts mid-job** | `JobQueue._sync_init()` already marks stale `running` rows as `failed` ("stale: process restarted"). A summarization job lost this way is re-enqueued on the next pass from the same idempotent source, and re-running it cannot double-write. |
| **Index node offline / no summary found** | The splice lookup misses and the runtime falls back to **today's exact behaviour** — the oldest-dropped window from `build_context_window()` — with one WARNING log. No user-visible error, no blocked turn. This is the required degraded mode, not an afterthought. |
| **Live inference must not be interrupted** | Eligibility requires no active lease *and* idle, so a job is placed only on a node the cluster believes is free. The live path additionally never blocks on summarisation: enqueue is fire-and-forget and the only synchronous summarisation work is the S3 lookup. |
| **Raw truth is never lost** | The archive is append-only (`taosmd/archive.py`). Summaries are rebuildable from it, so a bad summarizer costs CPU, never data. |
| **Runaway CPU cost** | A summary nobody ever reads is wasted work. Cap per agent per hour (`summarization.max_jobs_per_hour`) and stop enqueuing when no segment has crossed the threshold. |
| **Summarisation must be off-able** | Per-agent `summarization.enabled: false` disables enqueue, splice, and the RAG call entirely — same per-agent config surface as `memory_mode` / `memory_config` in `config.py`. **What the default should be — on with a conservative threshold, or off until section 7's recall gate has numbers — is a product decision, not a design one: see open question 7.** The switch has to be able to express both; nothing else here depends on which way the decision goes. |

## 6. Slice plan

Lanes follow the `library-app.md` convention: backend slices are the hognek
lane; UI surfaces are fleet cards.

### S1 — Local compression behind an idle + no-lease gate

Depends on: nothing new (one node, existing `JobQueue`).

Scope: the `summary` store, the `summarize_segment` job type, and the
eligibility predicate from section 3 **evaluated for the local node only**. The
job runs on the controller's own node. No cluster dispatch, no splice — the live
path is byte-for-byte unchanged.

Acceptance:

- [ ] A segment older than the configured threshold enqueues exactly one
      `summarize_segment` job at `Priority.BACKGROUND`; a segment that has not
      crossed it enqueues nothing.
- [ ] With a `GpuLease` held on any resource of the producing node, the job
      **stays pending** — test injects a lease and asserts no execution.
- [ ] With `worker.load` above `idle_load_ceiling`, the job stays pending.
- [ ] A completed job writes one summary row with full provenance (owner
      `user_id`, `conversation_id`, `segment_id`, `source_sha256`, message-id
      range, model, producer node, UTC `created_at`), and the embedding's
      `metadata_json` carries the same owner.
- [ ] **Cross-user isolation:** with two users, an agent of the same name in
      each, and a colliding `conversation_id`/segment, user B's lookup returns
      none of user A's summaries, and B's summarisation of that segment adds its
      own row instead of colliding with A's.
- [ ] Re-running the same segment with unchanged content writes **zero** new
      rows; running it after the segment changes writes exactly one new row and
      leaves the old one in place.
- [ ] Raw messages are untouched: message-store and archive row counts are
      asserted unchanged across a summarization run.
- [ ] `agents[].summarization.enabled: false` produces no enqueue and no
      summary rows for that agent.
- [ ] A metrics surface reports, per agent: segments condensed, compression
      ratio (source tokens ÷ summary tokens), CPU time spent.
- [ ] Duplicate completion is a no-op, not an error: a second commit of the same
      `(user_id, conversation_id, segment_id, source_sha256)` raises nothing and
      adds no row (explicit `ON CONFLICT ... DO NOTHING`).
- [ ] Crash between the summary commit and the qmd upsert leaves a **pending
      index-write outbox row**; a reconciler drains it and the index entry
      appears. Until then the summary row still exists and is still usable for a
      splice.
- [ ] Golden test: `build_context_window()` output is identical with the
      feature enabled and disabled.

### S2 — Cross-node dispatch

Depends on: S1; the worker-side job surface (see open question 1).

Scope: the job runs on an **eligible remote node**. Placement reads the
capability map + the eligibility predicate; the controller ships the segment
text (or the #154 state handle) and commits the returned result. This slice
also adds the mid-job recovery the §5 table defers to it: a claim TTL/reaper
(or re-enqueue from the idempotent source) so a worker stalled offline is
recovered without waiting for a controller restart.

Acceptance:

- [ ] Placement never selects a node with an active lease on any of its
      resources, a draining node, a stale-heartbeat node, or a `kind="device"`
      node.
- [ ] The lease-absence signal is corroborated before it is trusted: either
      every GPU-capable inference path (including the `TAOS_LLM_GATEWAY=1`
      `/api/llm/v1/chat/completions` route) claims a visible lease, or the
      predicate also checks the node's `free_vram_mb` / `load` sample — with a
      test that a node whose GPU is busy through a **non-leasing** path is not
      selected.
- [ ] An end-to-end round trip (enqueue → remote produce → controller commits
      summary row + index entry) passes against a stubbed worker, and the
      committed row carries the owner.
- [ ] The owner is **controller-resolved, not producer-asserted**: a stubbed
      producer whose result payload names a different `user_id` is committed
      under the registry-resolved owner, and the row that payload claims is
      left unchanged.
- [ ] Worker offline mid-job → job re-queued after TTL, **no partial row**, and
      the retry writes at most one row.
- [ ] The producer node performs **no** writes to controller stores (asserted:
      it has no store handle).
- [ ] The live inference path issues no summarisation RPC (asserted by
      call-counting the live turn path).
- [ ] Placement output appears in the Cluster app's node view with an
      eligibility reason, using the #897 surface rather than a new one.

### S3 — Splice into live context + RAG pull-back

Depends on: S2; #154 (handles) and #155 (locality routing) landing.

Scope: when a conversation references a segment outside the hot window, the
runtime retrieves that segment's summary through the ordinary RAG path and
splices a marked block ahead of the hot window.

Acceptance:

- [ ] Retrieval goes through the memory/RAG proxy with the collection's
      `locality_policy` respected; no bespoke shard fetch is added. The direct
      summary-store fallback (section 4.4) is exercised by a test in which the
      index write is still pending and the summary must still splice.
- [ ] A committed summary is spliceable **before** its index write is
      reconciled (two-tier lookup), and a stale summary is rejected at both
      tiers. The direct tier keys on the current segment's `source_sha256`, so a
      superseded row cannot shadow the current summary — covered by a test with
      two rows for one segment (old hash + current hash, index pending).
- [ ] Both lookup tiers are **owner-scoped**: a splice performed as user B for a
      conversation and agent name that collide with user A's resolves nothing
      (no cross-user splice), and the RAG tier queries B's own index rather than
      the shared default.
- [ ] A summary is never spliced for a segment whose messages are still inside
      the hot window (no duplication).
- [ ] Spliced text is marked and attributable: segment id + time range are
      present in the injected block.
- [ ] Stale summary (`source_sha256` mismatch) is ignored and the segment is
      re-queued for summarisation; the live/raw text is used for that turn.
- [ ] With no summary available, or the index node offline, behaviour is
      identical to the pre-feature baseline (oldest-dropped window) plus one
      WARNING — covered by a regression test that asserts the same context
      window in both cases.
- [ ] Per-agent disable suppresses enqueue, splice, and the RAG call.
- [ ] Dashboard/Observatory shows compression ratio and segments condensed per
      agent (issue acceptance criterion).

## 7. Pre-registered quality gate (not a slice)

Summarization that silently loses the facts a user refers back to is worse than
truncation, because it looks like memory. Before S3 becomes a default, an eval
must be run and published — same discipline as the Library spike's
pre-registered VMAF criteria:

- Fixed evaluation set chosen **before** the code exists: conversations with a
  known set of later questions whose answers live only in old segments.
- Measure: answer recall from the spliced summary vs answer recall from the raw
  segment (the ceiling). Per-agent, on the target tier.
- **Ship criterion:** summarised-context recall ≥ 90% of raw-context recall, and
  compression ratio ≥ 3× on the eval set. Below either number, the feature stays
  a flagged experiment and the numbers are published in the research notes.

## 8. Non-goals (v1)

- **No summarisation of the hot window.** Only segments past the threshold.
- **Not a replacement for the zero-loss archive.** Nothing here deletes or
  rewrites raw history.
- **Not distributed inference.** The summariser is a normal LLM call on one
  node, never a shard of a larger model.
- **No cross-vendor KV transfer.** Hardware-level cache movement is
  `docs/design/peer-vram-kv-cache.md`'s problem, not this one.
- **Not the #154 handle store and not the #155 router.** This consumes both and
  defines neither.
- **No GPU-first placement.** GPU nodes are eligible but ranked last behind CPU
  nodes, because their scarcity is the cluster's real constraint.
- **No new retrieval API.** Skimming summaries is a RAG call; that is the only
  query path.

## 9. Open questions / risks

1. **The worker-side job surface does not exist yet.** The worker's HTTP API is
   deliberately tiny: `POST /api/worker/deploy` (fixed `ALLOWED_COMMANDS`) and
   `POST /api/worker/remote` (command-prefix allowlist) — see
   `tinyagentos/worker/deploy.py` and `routes/cluster.py`. S2 must either add a
   narrow, typed job endpoint to the worker or route the work through the
   existing inference backends via `TaskRouter` / `POST /api/cluster/route`.
   **This is the one build-vs-reuse decision that should be made before S2 is
   dispatched.**
2. **Landing order.** S3 needs #154 and #155. If those slip, S1/S2 still deliver
   value (cheaper compression, observable metrics) and should not be held.
3. **Idle-signal quality.** `WorkerInfo.load` is self-reported; leases are the
   only hard signal. If idle detection proves unreliable, the fallback is to
   gate on leases plus a short "no in-flight job" window rather than trusting
   `load`.
4. **Never-read summaries.** Waste, not harm. Mitigated by the per-hour cap and
   the threshold; watch the metrics before making the threshold more aggressive.
5. **Multi-controller.** Out of scope — the archive index and summary store
   assumes one authoritative controller, which matches the current design of
   `tinyagentos/scheduling/mesh_sync.py` ("Controller is authoritative (source
   of truth)").
6. **Lease coverage is incomplete today.** Condition 5 only sees leases created
   through `ClusterManager.claim_lease`. GPU-capable paths that bypass it — the
   `TAOS_LLM_GATEWAY=1` chat-completions route is the known one — can occupy a
   GPU invisibly, so "no lease" is not yet proof of idleness. S2 must either
   close that gap (all GPU-capable inference paths claim/renew/release a visible
   lease) or corroborate lease absence with the VRAM/load sample. **Decide this
   alongside open question 1, before S2 is dispatched.**
7. **Default on, or default off?** A product decision for Jay, not a design
   constraint. Default-on lowers time-to-value but puts a compression path in
   front of every conversation before the section 7 recall gate has numbers to
   justify it; default-off makes the feature invisible until a user opts in per
   agent. Both are implementable on the same per-agent `summarization.enabled`
   surface that `memory_mode` already uses, so the design does not block on the
   answer — but section 5 must not assert a default before it is made.

## 10. What has to be true for this to be worth building

- Segments genuinely outlive the hot window for real agents on real hardware
  (measurable today from the archive: how often does a conversation exceed the
  window and then get asked about an old topic?).
- Idle CPU capacity actually exists on typical deployments — a single-Pi install
  has none, so the feature must be a no-op, not a regression, there.
- The section 7 recall gate passes. If it does not, the honest outcome is to
  keep truncation and publish why.
