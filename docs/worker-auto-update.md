# Worker Auto-Update: Drain Protocol + State Lifecycle

Design document for taOS Issue [#890](https://github.com/jaylfc/taOS/issues/890) — worker-initiated
auto-update with graceful pause, drain, install, restart, re-register, and rollback.

**Status:** Design proposal  
**Related PR:** [#1878](https://github.com/jaylfc/taOS/pull/1878) (controller-pushed `update-all` rolling orchestration — merged, extended here)

---

## 1. Worker Status Lifecycle

### Current states (`WorkerInfo.status` in `worker_protocol.py`)

```
online  — accepting work, heartbeating
offline — unreachable, marked by monitor loop after HEARTBEAT_TIMEOUT (30s)
busy    — defined in the enum comment but unused in any code path
draining — controller-initiated; no new tasks routed, existing leases run to completion
```

The heartbeat method (`manager.py:217`) explicitly preserves `draining`:
```python
if worker.status != "draining":
    worker.status = "online"
```

The `_worker_for_resource()` and `get_workers_for_capability()` guards already exclude draining
workers from routing (only `"online"` workers are eligible).

### New states for auto-update

```
online           — unchanged
offline          — unchanged
busy             — unchanged (unused today; available for future scheduling)
draining         — unchanged (controller-initiated path; also used by worker-initiated flow as
                   an intermediate state)
updating         — NEW: worker-initiated. Worker has begun install+restart cycle. Set by worker
                   itself via status endpoint, NOT by the controller. The controller treats
                   `updating` like `draining` for routing purposes (no new work).
update-available — NEW: informational. Worker has detected a new version but hasn't started
                   updating yet. Set by worker's update-check service. Surfaces in cluster UI
                   so admins can trigger the update manually or let the worker decide.
```

### State transition diagram (worker-initiated flow)

```
 online ───[detected new version]───> update-available
                                           │
                                     [worker initiates update]
                                           │
                                           ▼
 online ───[worker signals update]───> updating
                                           │
                                    [drain in-flight jobs]
                                           │
                                    [install + restart]
                                           │
                                    [re-register + heartbeat]
                                           │
                                           ▼
                                        online
                                           │
                                    [health-check fails]
                                           │
                                           ▼
                          updating ──[worker signals rollback]
                                           │
                                    [restore checkpoint]
                                           │
                                    [restart + re-register]
                                           │
                                           ▼
                                        online

Failure paths:
  - Worker in `updating` stops heartbeating → monitor loop marks `offline` after timeout
  - Worker in `update-available` stops heartbeating → monitor loop marks `offline` after timeout
  - Connection refused during deploy → worker status stays `updating`; monitor loop will mark
    `offline` after timeout, then worker re-registration marks `online`
```

### Compatibility with controller-pushed update

The existing `POST /api/cluster/workers/{name}/update` (routes/cluster.py:1194) is preserved
unchanged. It maps to:

```
 online ───[admin trigger]───> draining ──[deploy update-worker]──> (restart) ──> online
```

The worker-initiated flow is parallel and does NOT break the controller-pushed path.

---

## 2. Controller-Worker Signaling Protocol

### Sequence: worker-initiated auto-update

```
WORKER                          CONTROLLER
  │                                │
  │  1. version-check (poll)       │
  │  GET /api/version-check        │
  │  ← latest_version, channel     │
  │                                │
  │  2. POST /api/cluster/heartbeat│
  │  {status: "update-available",  │
  │   update_to_version: "..."}    │
  │  ──────────────────────────>  │
  │                                │  Sets worker status to "update-available"
  │                                │  Surfaces in cluster UI
  │                                │
  │  3. POST /api/cluster/workers/ │
  │     {name}/begin-update        │
  │  ──────────────────────────>  │
  │                                │    - Validates worker is in update-available
  │                                │    - Sets worker status to "updating"
  │                                │    - Initiates drain (if graceful)
  │                                │    - Returns 200 {acknowledged: true}
  │  ←  200 {acknowledged} ────── │
  │                                │
  │  4. Drain in-flight jobs (if   │
  │     graceful) — wait for leases│
  │     to expire/release          │
  │                                │
  │  5. POST /api/cluster/workers/ │
  │     {name}/update-ready        │
  │  ──────────────────────────>  │
  │                                │    - Confirms all in-flight work done
  │                                │    - Worker is clear to install+restart
  │  ←  200 {ready: true} ─────── │
  │                                │
  │  6. Install + restart          │
  │  (taos-deploy-helper,          │
  │   same as update-worker cmd)  │
  │                                │
  │  *** SERVICE RESTARTS ***      │
  │                                │
  │  7. POST /api/cluster/workers  │
  │  (re-register)                 │
  │  ──────────────────────────>  │
  │                                │    Sets worker status to "online"
  │  ←  200 {registered} ──────── │
  │                                │
  │  8. POST /api/cluster/heartbeat│
  │  ──────────────────────────>  │
  │                                │
  │  9. POST /api/cluster/workers/ │
  │     {name}/update-outcome      │
  │  {outcome: "success" |         │
  │   "rollback",                  │
  │   from_version, to_version}    │
  │  ──────────────────────────>  │
  │                                │    Records update outcome
  │                                │    Notifies admin if rollback
```

### Key design decisions

- **Worker pulls, worker initiates.** The worker detects the update, the worker decides when to
  start (or asks the admin), the worker signals each state transition. The controller never
  pushes an update without consent. This avoids a controller-initiated update landing on a
  worker mid-critical-job.

- **Graceful by default.** The worker waits for in-flight leases to complete before
  installing. The `force` flag on `begin-update` allows immediate drain for emergencies
  (mirrors `drain_worker(graceful=False)`).

- **Worker controls restart timing.** After `update-ready`, the worker installs and restarts
  at its own pace. The controller just needs to know the update has started (`updating` status)
  so it stops routing work. The heartbeat timeout (30s) is the natural safety net — if the
  worker never comes back after `update-ready`, the monitor loop marks it offline.

- **Status is worker-owned.** `update-available` and `updating` are set by the worker's
  heartbeat/status endpoints, not by the controller. This mirrors how `online` is set by
  the heartbeat today (except `draining` is preserved).

---

## 3. API Surface Changes

### Worker-side endpoints (new)

#### `POST /api/worker/update-check`
Self-triggered version check. Runs the same logic as the controller's `AutoUpdateService._probe_remote()`
but for the worker's own install.

**Request:** (HMAC-signed, optional auto-trigger)
```json
{}
```

**Response:**
```json
{
  "current_version": "96a33cb6",
  "latest_version": "b7f12d3a",
  "update_available": true,
  "channel": "dev",
  "last_checked": 1784306800
}
```

#### `POST /api/worker/update-check/trigger`
Admin-triggered version check. Does NOT start update — just refreshes the check and reports.

#### `POST /api/worker/pre-update-checkpoint`
Creates a rollback checkpoint before starting the update. Called internally by the worker's
update orchestrator (not exposed on the controller or external API).

```json
{
  "checkpoint_tag": "taos-worker-pre-update-<ts>"
}
```

#### `POST /api/worker/update-health-check`
Called by the worker after restart to verify the new install is healthy before reporting
`update-outcome: "success"`. Checks:
- Worker process is running
- Backend detection succeeds
- Controller is reachable
- Heartbeat succeeds

Returns `{"healthy": true}` or `{"healthy": false, "reason": "..."}`.

### Controller-side endpoints (new)

#### `POST /api/cluster/workers/{name}/begin-update`
Worker signals it wants to start updating. Controller validates state, marks as `updating`,
initiates drain.

**Request:** (HMAC-signed by worker)
```json
{
  "to_version": "b7f12d3a",
  "force": false,
  "checkpoint_tag": "taos-worker-pre-update-1784306800"
}
```

**Response:** `200`
```json
{
  "worker": "gpu-node",
  "status": "updating",
  "previous_status": "update-available",
  "drain_started": true,
  "acknowledged": true
}
```

**Errors:**
- `409` — worker not in `update-available` or `online` status
- `404` — worker not found

#### `POST /api/cluster/workers/{name}/update-ready`
Worker signals its in-flight jobs are done and it's about to install+restart.

**Request:** (HMAC-signed by worker)
```json
{
  "to_version": "b7f12d3a"
}
```

**Response:** `200`
```json
{
  "ready": true,
  "worker": "gpu-node",
  "status": "updating"
}
```

#### `POST /api/cluster/workers/{name}/update-outcome`
Worker reports the outcome after restart + health-check.

**Request:** (HMAC-signed by worker)
```json
{
  "outcome": "success",
  "from_version": "96a33cb6",
  "to_version": "b7f12d3a",
  "health_check_passed": true
}
```

Or for rollback:
```json
{
  "outcome": "rollback",
  "from_version": "96a33cb6",
  "to_version": "b7f12d3a",
  "rollback_to": "96a33cb6",
  "health_check_passed": false,
  "failure_reason": "Backend detection failed after restart: no ollama service"
}
```

**Response:** `200`
```json
{
  "recorded": true,
  "worker": "gpu-node",
  "status": "online"
}
```

### Controller-side heartbeat change

Extend the `HeartbeatBody` model (routes/cluster.py:243) and `cluster.heartbeat()` to accept an
optional `update_status` field:

```python
class HeartbeatBody(BaseModel):
    # ... existing fields ...
    update_status: str | None = None  # "update-available", "updating", or None
    update_to_version: str | None = None
```

When `update_status` is set, the heartbeat handler updates the worker's status in
`ClusterManager._workers` accordingly. This is the mechanism for the worker to set
`update-available` and `updating` without needing a dedicated endpoint for every state change.

**Important:** The existing heartbeat drain-preservation logic must be extended. Currently:
```python
if worker.status != "draining":
    worker.status = "online"
```

After this change:
```python
if worker.status not in ("draining", "updating"):
    worker.status = "online"
```

`update-available` is informational only — workers in `update-available` are still `online`
for routing purposes. The worker sets it via the `update_status` field but the heartbeat
doesn't need to preserve it (it's a soft signal, not a routing gate).

### Routing impact

All existing routing gates in `ClusterManager` already use `worker.status == "online"`:
- `_worker_for_resource()` (line 340)
- `get_workers_for_capability()` (line 579)
- `aggregate_catalog()` (line 622)

No changes needed — `updating` workers are automatically excluded from routing, same as
`draining` workers today.

---

## 4. Existing Infrastructure to Extend

### `AutoUpdateService` (controller side, `auto_update.py`)

The controller's `AutoUpdateService` polls git every hour. The worker needs a structurally
similar service, but:

| Aspect | Controller | Worker |
|--------|-----------|--------|
| Version source | `git fetch origin/<branch>` | Mirror of controller check — the worker asks the controller what the latest version is, or does its own `git fetch` if it has a clone |
| Check interval | 60 min (production) | Configurable, default 60 min |
| Notification | Fires `system.update` notification in UI | Sets `update_status` on heartbeat → surfaces in cluster UI |
| Install path | `update_runner.py` → `git merge --ff-only` | `taos-deploy-helper update-worker` (existing `worker/deploy.py` path) |

**New file:** `tinyagentos/worker/update_check.py` — mirrors `AutoUpdateService._probe_remote()`
pattern but for the worker's install. Can either:
- Poll the controller's `/api/version-check` endpoint, OR
- Poll the same git remote directly if the worker has a repo clone

The task (C1) will implement this; the design doc just defines the interface.

### `POST /api/worker/deploy {"command": "update-worker"}` (worker side, `worker/deploy.py`)

Already exists and is the install+restart mechanism. The `update-worker` command runs
`taos-deploy-helper update-worker` with passwordless sudo. The worker orchestrates the full
sequence:

```
1. pre-update-checkpoint (tag current version)
2. signal controller: begin-update
3. drain in-flight work
4. signal controller: update-ready
5. run taos-deploy-helper update-worker (existing command)
6. service restarts
7. re-register
8. health-check
9. signal controller: update-outcome (success or rollback)
```

If step 8 fails, the worker restores the checkpoint and signals `update-outcome: "rollback"`.

### `ClusterManager.drain_worker()` / `cancel_drain()` (`manager.py`)

The new `begin-update` endpoint calls `drain_worker(name, graceful=True)` internally — no code
change needed. The monitor loop's drain auto-completion (lines 702-721) already handles the
transition from `draining` → `offline` when all leases are released.

**Addition:** The monitor loop should also handle `updating` workers that stop heartbeating.
Currently only `online` and `draining` are swept. The `updating` status is a special case:
a worker that goes from `updating` → heartbeat timeout should be marked `offline`, same as
`draining`. The existing draining timeout logic (lines 722-751) covers this naturally if we
add `updating` to the statuses that trigger stale-drain:

```python
elif worker.status in ("draining", "updating"):
    # existing drain/timeout logic...
```

### PR #1878's `update-all` rolling orchestration

PR #1878's batch update endpoint (`POST /api/cluster/update-all`) uses the controller-pushed
`POST /api/cluster/workers/{name}/update`. The worker-initiated flow is independent — the
controller's `update-all` can `POST` each worker's update endpoint, but the worker itself
may choose to self-update before the controller pushes. No conflict: both paths converge
on the same drain → install → restart → re-register sequence.

---

## 5. Rollback Strategy

### Pre-update checkpoint

Before starting the install, the worker creates a recovery tag:

```bash
git tag taos-worker-pre-update-<timestamp> HEAD
```

This mirrors the controller's `update_runner.py` pattern (`taos-pre-update-<sha>-<ts>`).

### Health-check after restart

The worker MUST verify the new install is healthy before reporting success:

1. **Internal checks:**
   - Worker process is running (systemd `ActiveState=active`)
   - `detect_backends()` succeeds (at least one backend reachable)
   - Worker can reach the controller (HTTP ping)

2. **Controller-acknowledged check:**
   - `POST /api/cluster/workers` registration succeeds
   - `POST /api/cluster/heartbeat` returns 200
   - Backend catalog is populated (heartbeat carries full catalog)

3. **Grace period:** After restart, the worker waits 15 seconds before health-check to let
   all backends stabilize (Ollama loading models, llama.cpp warming up, etc.).

### Rollback procedure

If ANY health-check fails:

```bash
# 1. Stop the worker service
systemctl stop taos-worker

# 2. Restore the checkpoint
git checkout taos-worker-pre-update-<timestamp>
# or: git reset --hard taos-worker-pre-update-<timestamp>

# 3. Start the worker service
systemctl start taos-worker

# 4. Worker re-registers with the controller (normal run loop)
#    This sets status back to "online"

# 5. Worker signals update-outcome: "rollback"
POST /api/cluster/workers/{name}/update-outcome
{
  "outcome": "rollback",
  "from_version": "<old>",
  "to_version": "<new>",
  "rollback_to": "<old>",
  "failure_reason": "health-check: backend detection failed"
}
```

### Safeguards

- **Checkpoint tag is never deleted automatically.** If the worker crashes before reaching
  health-check, the tag persists and a human operator can `git checkout` it manually.
- **Rollback is idempotent.** If the worker already rolled back, additional rollback attempts
  are no-ops (the tag points to a commit already checked out).
- **Controller is notified regardless.** Even if the worker can't reach the controller for
  `update-outcome`, the re-registration + heartbeat after rollback will set the worker back
  to `online`. The controller sees "worker went offline briefly, then came back" — the
  admin UI surfaces the update failure from the last successful `update-outcome` POST.

### Systemd integration

The `taos-deploy-helper update-worker` command already handles the restart. For rollback,
a separate helper subcommand or the worker agent itself orchestrates the git operations.
The deploy helper runs with passwordless sudo; the worker agent calls it with:
```bash
sudo /usr/local/bin/taos-deploy-helper update-worker
```

A new helper command `rollback-worker` could be added:
```bash
sudo /usr/local/bin/taos-deploy-helper rollback-worker <checkpoint-tag>
```

---

## Implementation Plan

### C1: Worker version-detection service (`t_2f23c276`)
- `tinyagentos/worker/update_check.py` — periodic version poll
- Mirror `AutoUpdateService._probe_remote()` pattern
- Set `update_status: "update-available"` on heartbeat

### C2: Worker-initiated graceful pause + drain (`t_33babf4b`)
- New controller endpoints: `begin-update`, `update-ready`
- Extend heartbeat to accept `update_status`
- Extend heartbeat drain-preservation for `updating` status
- Monitor loop: handle `updating` workers that stop heartbeating

### C3: Self-install + restart + re-register + rollback (`t_2116c3d5`)
- `POST /api/worker/update-check`, `/update-health-check`, `/pre-update-checkpoint`
- `POST /api/cluster/workers/{name}/update-outcome`
- Rollback orchestration (checkpoint → install → health-check → rollback on failure)
- `update-worker` deploy command already exists — reuse it

### Execution order

```
C0 (this doc) → C1 (version detection)
              → C2 (drain protocol)
              → C3 (install + rollback, depends on C2's drain being ready)
```

C1 and C2 can run in parallel. C3 waits on C2 (install after drain protocol is agreed).

---

## Appendix: Why worker-pulled, not controller-pushed

The existing `POST /api/cluster/workers/{name}/update` is controller-pushed: an admin clicks
"Update" and the controller drains the worker, sends the deploy command, and waits.

The worker-initiated flow is **worker-pulled** because:

1. **Workers run on heterogeneous hardware** (GPU boxes, Pi workers, RK3588 NPUs, macOS,
   Windows). A version that works on one worker may break another. The worker knows its own
   platform and can check compatibility before attempting an update.

2. **Timing autonomy.** A worker mid-large-inference should not be force-updated. The worker
   knows its own load and can pick a quiet moment (or wait for admin approval).

3. **Headless workers.** Some workers are unattended. Auto-update with rollback means they
   keep themselves current without human SSH intervention — the core goal of Issue #890.

4. **Both flows coexist.** Controller-pushed is for admin-orchestrated batch updates (PR #1878's
   `update-all`). Worker-pulled is for self-maintaining unattended nodes. They converge on the
   same drain → install → restart → re-register sequence.
