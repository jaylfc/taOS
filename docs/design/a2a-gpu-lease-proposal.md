# RFC: Shared-GPU Lease Coordination on the TAOS Controller (#893)

**Author:** Hogne (Skald platform, taOS fork)  
**Date:** 2026-07-06  
**Status:** Draft — seeking jaylfc buy-in before prototyping  
**Target:** taOS controller endpoints + Skald TaosDispatcher integration

---

## Problem

Skald's `TaosDispatcher._trigger_load()` calls `POST /api/system/load-model` on the
Skald server — a heavyweight operation that kills running processes, waits for VRAM
to drain, and starts a new backend process. The dispatcher fires this *before checking
whether the target worker has enough free VRAM*.

taOS now surfaces real-time VRAM per worker (`free_vram_mb` / `used_vram_mb` on
`/api/cluster/workers`, shipped in #894), but that data is a **snapshot**, not a
**reservation**. Two concurrent requests can both see "8 GiB free", both fire
load-model, and one loses the race — the loser's load fails or (worse) OOM-kills
the winner.

The gap: **no coordination primitive on the controller** that lets an external
caller (Skald, any A2A agent, future tool servers) atomically *reserve* GPU capacity
before committing to a load.

## Proposal: Lease API on `/api/cluster`

Add two endpoints to the taOS controller:

### `POST /api/cluster/leases/claim`

**Request body:**

```json
{
  "resource_id": "worker-name:gpu-cuda-0",
  "ttl_seconds": 30,
  "caller": "skald-dispatcher"
}
```

`resource_id` follows the convention `{worker_name}:{resource_name}`. The `worker_name`
portion is the registered worker name; the `resource_name` portion is the stable resource
identifier (e.g. `gpu-cuda-0` for a single-GPU host, `gpu-cuda-0` / `gpu-cuda-1` for
multi-GPU). The caller provides whatever label is meaningful.

`ttl_seconds`: maximum lease duration. If the caller doesn't release or renew within
this window, the lease expires and the reservation is freed. Typical value: 30s for
a model-load check, 120s for the load itself.

**Pre-claim check:** The controller checks the target worker's current `free_vram_mb`
(from the most recent heartbeat) against the VRAM the caller says it needs. If
`free_vram_mb < required_vram_mb`, the claim is refused with 409 Conflict.

**Response (200):**

```json
{
  "status": "claimed",
  "lease_id": "l_a1b2c3d4",
  "resource_id": "hognehermes:gpu-cuda-0",
  "expires_at": 1783350000.0,
  "ttl_seconds": 30,
  "free_vram_mb": 6144,
  "used_vram_mb": 2048
}
```

**Response (409):**

```json
{
  "error": "resource already leased",
  "lease_id": "l_xxxxxxx",
  "holder": "skald-dispatcher",
  "expires_at": 1783349975.0
}
```

### `POST /api/cluster/leases/release`

```json
{
  "lease_id": "l_a1b2c3d4"
}
```

**Response (200):**

```json
{
  "status": "released",
  "lease_id": "l_a1b2c3d4"
}
```

Idempotent — releasing an already-expired or unknown lease returns 200 (no error).

### `POST /api/cluster/leases/renew`

```json
{
  "lease_id": "l_a1b2c3d4",
  "ttl_seconds": 30
}
```

For long-running model loads (120s swap + health poll), the caller can heartbeat the
lease to extend it. Returns 409 if the lease has expired.

## Modelling

Leases live in the controller's in-memory `ClusterManager` alongside the worker
registry — no external store needed, no Persistence. A lease is a lightweight struct:

```python
@dataclass
class GpuLease:
    lease_id: str
    resource_id: str        # "worker-name:gpu-cuda-0"
    caller: str             # "skald-dispatcher"
    holder: str             # remote IP or caller label
    expires_at: float       # monotonic timestamp
    required_vram_mb: int   # how much VRAM the caller requested
```

The controller cleanup task (`_monitor_loop`) sweeps expired leases on its existing
5-second tick — same loop that marks workers offline.

### Why in-memory and not Postgres?

- Short-lived (30–120s), ephemeral data with no durability requirement.
- Controller restart already resets the entire cluster view — leases lost in a
  restart are harmless because the workers reconnect and re-advertise fresh VRAM
  anyway.
- Avoids adding a new table and cleanup logic for something that is never queried
  historically.

### Why on the controller, not on the worker?

The controller is the single source of truth for the cluster view. Workers might be
behind NAT (no inbound reachability for claim/release). The controller aggregates
VRAM data from heartbeats and is already the coordination point for worker
registration.

## Skald Integration

### What changes in TaosDispatcher

Current code in `_route_taos()` (taos.py:128–228):

1. Fetches workers from `GET /api/cluster/workers`
2. Separates candidates into "already loaded" and "available but not loaded"
3. Sorts by load + total VRAM
4. If an available-candidate is picked, fires `_trigger_load()` without any lease

Proposed change:

1. Fetch workers (unchanged)
2. Separate loaded / available candidates (unchanged)
3. Sort by load + **free VRAM** (not total VRAM — taOS #894 makes this possible)
4. Before picking an available-candidate, attempt to `POST /api/cluster/leases/claim`
   for the candidate worker with the model's VRAM requirement
5. Only if the claim succeeds, proceed to `_trigger_load()`
6. Store the `lease_id` alongside the load trigger so the scheduler can release it
   when the model is healthy (or on error)

```python
# Pseudocode for TaosDispatcher._route_taos:
if not loaded_candidates and available_candidates and requested_model:
    best = available_candidates[0]  # after VRAM-aware sort
    worker_name = best["name"]
    resource_id = f"{worker_name}:gpu-cuda-0"
    required_vram = _lookup_vram(requested_model)  # from gpu_models.yaml
    lease = await self._claim_lease(resource_id, required_vram_mb=required_vram)

    if lease:
        ok = self._trigger_load(requested_model, resource_id)
        if ok:
            self._pending_lease = lease["lease_id"]
        else:
            self._release_lease(lease["lease_id"])
    else:
        # Try next candidate, or fall back
        ...
```

### What changes in Skald's `load_model` endpoint

Currently `POST /api/system/load-model` does an **in-process** `ModelSwapper.ensure_software()`.
The swap happens synchronously inside the HTTP handler, blocking up to 120s.

The lease-aware flow:

1. Skald server receives the request, reads `lease_id` from the body
2. Skald spawns the swap in a *background worker* (following the pattern already
   in `model_swapper_worker.py`)
3. The endpoint returns 202 Accepted immediately with the lease_id
4. The background worker polls `POST /api/cluster/leases/renew` to keep the lease
   alive during the swap
5. On swap complete: the worker releases the lease
6. On swap fail: the worker releases the lease, and the scheduler retries

The dispatcher / scheduler polls the model gate (as today) to detect when the model
is healthy; the lease just prevents another caller from also triggering a load.

## API Surface Summary

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/cluster/leases/claim` | Reserve GPU capacity for TTL seconds |
| POST | `/api/cluster/leases/release` | Free a reservation |
| POST | `/api/cluster/leases/renew`  | Extend lease TTL |
| GET  | `/api/cluster/leases`       | List active leases (diagnostics) |

All endpoints are **unauthenticated** (same as the rest of the cluster API — the
controller is on a LAN-only port). If auth is added to the cluster API later,
leases inherit the same guard.

## Edge Cases

### Lease holder crashes
The TTL handles this. After `ttl_seconds`, the lease expires in the monitor loop
and the resource is freed. The crashed caller's model-load might still be running,
but that's a local problem on the Skald side (swapper detects stale locks already).

### Two callers race for the same resource
The `claim` endpoint is a synchronous in-memory operation under the asyncio lock
on the controller — no two claims for the same resource_id can succeed.

### Worker goes offline with active lease
When a worker is marked offline in `_monitor_loop` (30s no heartbeat), all leases
for that worker's resources are released. The next heartbeat from a stale lease
holder gets a 404, and it releases its local state.

### Caller asks for more VRAM than the model actually needs
The pre-claim check uses the `required_vram_mb` from the request body. The caller
(Skald) should look this up from `gpu_models.yaml`'s `vram_required_gb` field.

### VRAM snaphot is stale (last heartbeat was 5s ago)
In the worst case: heartbeat at T+0 reports 8 GiB free, model load starts at T+5
consuming 6 GiB, Skald claims at T+5.5 — the claim succeeds based on stale data,
but the worker's actual free VRAM is now 2 GiB.

**Mitigation:** The lease is advisory, not a hard guarantee. The real check is the
Skald model swapper's `ensure_software()` which probes actual GPU state on the
worker before launching. The lease prevents the *blind double-fire* problem; it
doesn't replace the worker-side GPU probe.

If this becomes a meaningful gap in practice, the controller can validate at
claim time by comparing `required_vram_mb + worker_used_vram_mb` against the
hardware's `total_vram_mb` rather than relying solely on `free_vram_mb` from the
last heartbeat. But `free_vram_mb` is good enough for the 80% case.

### Multiple GPU resources on one worker
`resource_id = "worker-name:gpu-cuda-0"` vs `"worker-name:gpu-cuda-1"` allows
per-GPU leases. The free/used VRAM fields on WorkerInfo are per-worker (first GPU
only today — `gpu_vram_snapshot()` probes GPU 0). Multi-GPU workers would need the
heartbeat to report per-GPU VRAM before per-GPU leases work correctly; until then,
a single `gpu-cuda-0` resource_id represents the worker's GPU capacity.

## Implementation Plan (prototype on fork)

### Phase 1: Controller lease API (taOS fork)

1. Add `GpuLease` dataclass to `worker_protocol.py`
2. Add lease registry (`_leases: dict[str, GpuLease]`) to `ClusterManager`
3. Add `claim_lease()`, `release_lease()`, `renew_lease()` methods
4. Add lease expiry sweep to `_monitor_loop()`
5. Add routes in `routes/cluster.py`:
   - `POST /api/cluster/leases/claim`
   - `POST /api/cluster/leases/release`
   - `POST /api/cluster/leases/renew`
   - `GET /api/cluster/leases`
6. Tests (unit + integration)

### Phase 2: TaosDispatcher integration (Skald fork)

1. Add `_claim_lease()` / `_release_lease()` HTTP helpers to `TaosDispatcher`
2. Modify `_route_taos()` to sort by free VRAM (uses the #894 data already available)
3. Add lease claim/release around `_trigger_load()` calls
4. Store `_pending_lease` so the scheduler can release on task completion
5. Pass `lease_id` through to `/api/system/load-model` for background renew

### Phase 3: Background swap with lease renewal (Skald fork)

1. Refactor `/api/system/load-model` to spawn background swap + return 202
2. Background worker renews lease every 10s during swap
3. Release lease on completion/failure

## Alternatives Considered

### A: Do nothing — rely on the swapper's existing in-process lock
The Skald model swapper already has `swap_in_progress` in settings. This works for
a single Skald instance but does nothing when two different callers (e.g., Skald
+ a separate A2A agent) both try to use the GPU. The lease API is cheap to add
and unlocks multi-tenant GPU sharing.

### B: Put the lease logic entirely in Skald (Postgres settings)
Leases in the Skald DB only work if every GPU consumer talks to Skald. The point of
the taOS controller is to be the **infrastructure coordination hub** — putting the
lease there makes it available to any A2A agent on the LAN, not just Skald.

### C: Full distributed lock (etcd/consul/Redis)
Massive overkill. One controller, one LAN, sub-second lease TTL — in-memory on the
controller is all we need.

## Questions for jaylfc

1. **`resource_id` convention:** Does `{worker_name}:{resource_name}` work as a
   naming scheme? It mirrors the `resource_pool` convention in Skald but open to
   alternatives.

2. **VRAM pre-check vs. advisory:** Should the controller enforce VRAM at claim
   time (return 409 if `free_vram_mb < required_vram_mb`) or purely advisory?
   I lean toward enforcing — it prevents the most common failure mode.

3. **Auth boundary:** The cluster API is currently unauthenticated (LAN-only).
   Leases inherit this. Should lease endpoints require any caller identification
   beyond the `caller` field?

4. **Per-GPU VRAM in heartbeats:** For multi-GPU workers, the heartbeat currently
   reports only GPU 0. Extending `gpu_vram_snapshot()` to return an array of
   per-GPU dicts is a natural follow-on but out of scope for this RFC.

