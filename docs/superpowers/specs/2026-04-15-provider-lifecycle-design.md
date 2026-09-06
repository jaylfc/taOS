# Provider Lifecycle Management — Design Spec

**Date:** 2026-04-15
**Status:** Approved

## Problem

taOS providers (backends) are currently either always running or always broken
from the user's perspective. There is no per-provider on/off switch, no
concept of starting a service on demand, and no way to free NPU/GPU memory
when a service is idle. Novice users hit confusing errors when a service
isn't running; power users have no way to pin a service as always-on or
hand-manage it themselves.

Three compounding issues surfaced this:

1. `sd-cpp` and `rknn-sd` were missing from `VALID_BACKEND_TYPES` — fixed.
2. `RknnSdAdapter` probed a stale endpoint — fixed.
3. The RKNN SD provider had the wrong type and URL because registration was
   entirely manual — this spec fixes that permanently.

## Goals

- taOS-managed services self-register with correct type, URL, and lifecycle
  defaults at install time — no user configuration required.
- Each provider has a lifecycle policy: `enabled`, `auto_manage`,
  `keep_alive_minutes`.
- When `auto_manage` is on, taOS starts the service when a task needs it and
  stops it after the keep-alive window expires or when the scheduler needs the
  resource for something more urgent.
- Graceful drain on stop; "Kill now" escape hatch for immediate termination.
- `keep_alive_minutes: 0` means always on — service is never auto-stopped.
- Manifest defaults are beginner-friendly (auto manage on, 10 min keep-alive);
  advanced users override per-provider in config.

## Non-goals

- Managing third-party services not installed by taOS (user-pointed external
  backends are registered manually, lifecycle is their responsibility).
- Hot-swapping models within a running service.
- Cross-worker lifecycle orchestration (separate cluster spec).

## Data Model

### Three layers

**1. App catalog manifest** (ships with the service, read-only):

```yaml
# app-catalog/services/rknn-sd.yaml
name: rknn-sd
type: rknn-sd
default_url: http://localhost:7863
capabilities:
  - image-generation
lifecycle:
  auto_manage: true
  keep_alive_minutes: 10
  start_cmd: "systemctl start tinyagentos-rknn-sd"
  stop_cmd: "systemctl stop tinyagentos-rknn-sd"
  startup_timeout_seconds: 90
```

**2. `config.yaml` backends entry** (user overrides only — omit = use manifest default):

```yaml
backends:
  - name: local-rknn-sd
    type: rknn-sd
    url: http://localhost:7863
    enabled: true
    auto_manage: true        # optional override
    keep_alive_minutes: 0    # 0 = always on
```

**3. `BackendCatalog` runtime entry** (in-memory, merged manifest + config + live health):

Adds a `lifecycle_state` field to each catalog entry:

```
stopped → starting → running → draining → stopping → stopped
```

The catalog is already the authoritative runtime view of all backends.
Lifecycle state slots in alongside `status`, `models`, and `capabilities`.

### keep_alive_minutes semantics

| Value | Meaning |
|-------|---------|
| `0` | Always on — never auto-stop |
| `1–60` | Stop N minutes after last task completes |
| manifest default | `10` |

## Auto-Registration

When a taOS service installs via the app catalog, the installer reads the
manifest and writes a `config.backends` entry automatically — correct type,
URL, name, and lifecycle defaults. No user input required. The provider
appears in the UI as `Running` or `Stopped` immediately after install.

This is the permanent fix for the `rknn-sd` misconfiguration: the manifest
is the single source of truth, the installer is a consumer of it.

## LifecycleManager

New module: `tinyagentos/lifecycle_manager.py`

Single responsibility: start and stop services based on demand and policy.
Sits between the scheduler and the backend services.

### Start trigger

Scheduler submits a task for capability X → catalog has a backend for X that
is `stopped` with `auto_manage: true` → LifecycleManager:

1. Sets state to `starting`
2. Runs `start_cmd`
3. Polls `/health` up to `startup_timeout_seconds`
4. On success: sets state to `running`, scheduler retries routing
5. On timeout: sets state to `stopped`, task fails with clear error

### Stop trigger (two paths)

**Keep-alive expiry:**
- Timer fires after `keep_alive_minutes` of no in-flight tasks
- `keep_alive_minutes: 0` → timer never starts
- Scheduler can override timer early under resource pressure (e.g. NPU needed
  for higher-priority task while image gen is idle)

**Graceful stop flow:**
1. Set state to `draining` — no new tasks routed to this backend
2. Wait for in-flight tasks to finish (max 60 s)
3. Run `stop_cmd`
4. Set state to `stopped`

**Kill now:** Skips drain wait, runs `stop_cmd` immediately, sets `stopped`.

### Resource pressure eviction

The scheduler signals the LifecycleManager when a higher-priority task needs
a resource currently held by an idle-but-alive backend. LifecycleManager
initiates graceful stop even if the keep-alive timer hasn't expired. This
integrates with the SLO-aware scheduler's preemption model.

## API Changes

All changes are additive — no breaking changes to existing endpoints.

### `GET /api/providers`

Adds per-entry fields:
```json
{
  "lifecycle_state": "running",
  "auto_manage": true,
  "keep_alive_minutes": 10,
  "enabled": true
}
```

### `PATCH /api/providers/{name}` (new)

Update lifecycle settings without replacing the whole provider:
```json
{ "enabled": false, "auto_manage": true, "keep_alive_minutes": 0 }
```

### `POST /api/providers/{name}/start` (new)

Manually start a stopped provider. Returns immediately; client polls
`GET /api/providers` for state transitions.

### `POST /api/providers/{name}/stop` (new)

Graceful drain + stop. Body: `{ "force": false }`.
`force: true` = kill now — skips drain.

### `POST /api/providers/{name}/test` (existing)

No breaking change. If provider is `stopped` + `auto_manage: true`, starts
it first before probing.

## UI Changes

### Status chips

Extends existing type/status chips to include lifecycle state:

`Stopped` · `Starting…` · `Running` · `Draining…`

Spinner on transitional states (`starting`, `draining`).

### Lifecycle controls (provider detail page)

Below the existing Test / Edit / Delete buttons:

- **Enabled** toggle — off means never auto-started, excluded from routing
- **Auto manage** toggle — off means taOS leaves the service alone
  (health checks still run, routing still works if it happens to be up)
- **Keep alive** number input — visible only when auto manage is on.
  `0 = always on`, `1–60 = minutes`. Default: `10`.

### Contextual action buttons

Replace static Test/Edit/Delete layout with state-aware buttons:

| State | Primary button | Secondary |
|-------|---------------|-----------|
| `stopped` | Start | — |
| `starting` | (spinner, disabled) | — |
| `running` | Stop (graceful) | Kill (destructive, smaller) |
| `draining` | (spinner, disabled) | Kill |
| `stopping` | (spinner, disabled) | — |

### Error state messaging

Replaces raw error strings with human-readable messages and suggested actions:

| Condition | Message |
|-----------|---------|
| Stopped + auto_manage on | "Starting service…" (auto-triggered immediately) |
| Stopped + auto_manage off | "Service is stopped. Start it manually or enable Auto manage." |
| Can't reach URL | "Cannot reach `{url}`. Check the service is installed." |
| Start timed out | "Service did not respond within {timeout}s. Check logs." |

## Integration with Existing Design

- **BackendCatalog** (`scheduler/backend_catalog.py`) — gains `lifecycle_state`,
  `auto_manage`, `keep_alive_minutes` per entry. Already the runtime authority;
  lifecycle state is a natural addition.
- **Resource scheduler** (`scheduler/`) — signals LifecycleManager on resource
  pressure eviction. Reads `lifecycle_state` to exclude `stopped` backends
  from routing unless `auto_manage: true` (in which case it triggers start).
- **Restart orchestrator** (`restart_orchestrator.py`) — graceful drain reuses
  the same `prepare()` primitive already designed for agent shutdown.
- **App catalog** (`app-catalog/services/`) — manifest schema extended with
  `lifecycle` block. Installer updated to write `config.backends` entry on
  service install.

## Implementation Order

1. Extend `BackendCatalog` with `lifecycle_state`, `auto_manage`,
   `keep_alive_minutes` fields
2. Add manifest `lifecycle` block + update installer to auto-register
3. Implement `LifecycleManager` with start/stop/kill and keep-alive timer
4. Wire scheduler → LifecycleManager for demand-based start and eviction
5. Add `PATCH`, `start`, `stop` API endpoints
6. Update `GET /api/providers` response
7. UI: lifecycle controls, state-aware buttons, improved error messages
8. Fix `POST /api/providers/{name}/test` to auto-start if needed
9. Migrate existing taOS services (rknn-sd, rkllama) to manifest-based
   auto-registration
