# Cluster-aware Backend Service Management — Design

**Status:** Approved design (2026-07-09). Phase 1 to build now; Phase 2 spec'd, deferred.

## Goal

Installing a model-inference backend (rkllama, qmd, llama.cpp, vllm, ollama, ...) must reliably make it a **managed systemd service** on the node it lands on, and because backends migrate across cluster nodes, that management is **reconciled per node** (adopt/enable on arrival, stop/disable on departure). Backends expose a uniform start/stop/restart/health interface so recovery (#1743) and cluster placement/migration work the same everywhere.

## Motivation (audit, 2026-07-09)

- **qmd** is a proper managed systemd unit (installed + `enable --now` by `install-server.sh`, which every box runs). It survives reboots and is `systemctl`-restartable.
- **rkllama** on the production Pi was a **hand-started bare process** (PPID 1, launched from a login session, no unit) with no boot persistence, and the Activity "Restart AI Services" (#1743) could not restart it (`_restart_ai_unit` returns `"not installed"` when no unit exists).
- Root cause: the store wrapper `scripts/install-rkllama.sh` short-circuited `exit 0` the moment any process answered the port, before delegating to `install-rknpu.sh::install_systemd_unit()` — so a hand-start permanently blocked unit creation. **Fixed in PR #1755** (a live port only counts as installed if `rkllama.service` is enabled; otherwise fall through and adopt under systemd). That fix is the first brick of this design.
- The manifest contract is inconsistent: only 1 service manifest sets `auto_manage: true` (rkllama); rk-llama-cpp's installer creates a unit but its manifest says `auto_manage: false`; most services declare neither. taOS has no reliable declared view of which backends it manages.
- No reconciler ties "desired backend placement" to "managed unit state per node". The controller is HTTP/poll-only (health via `/api/tags` or `/health`, routing via LiteLLM) and delegates lifecycle to systemd, but nothing guarantees the unit exists.

## Existing building blocks (reuse, do not reinvent)

- **Worker agent + router** (`tinyagentos/cluster/router.py`, `worker_protocol.py`): the controller routes to each node's worker-agent HTTP API (`worker.url`); cross-host callers go through the agent, never dial a backend URL directly. Worker endpoints are protected by HMAC (`cluster/worker_auth.py`).
- **local_worker** (`cluster/local_worker.py`): the controller's own node acts as a worker in-process.
- **Capability heartbeat** (`routes/cluster_capability.py`): nodes report capabilities/status on a cadence.
- **Placement optimiser** (`cluster/optimiser.py`): advisory `PlacementSuggestion`s (not enforced).
- **Service migrator** (`cluster/service_migrator.py`): migrates LXC-hosted services; does NOT yet cover host systemd backends like rkllama/qmd.
- **#1743 recovery** (`routes/system.py::restart_ai_stack` / `_restart_ai_unit`): fails soft per unit, resolves user-vs-system scope with `systemctl cat`; today assumes a local unit.
- **Backend adapters** (`backend_adapters.py`): `OllamaCompatAdapter.health()` (rkllama), qmd `/health`.

## Decisions (locked)

1. **Scope:** Phase 1 now (uniform contract + per-node ensure/self-heal + #1743 rewire + Cluster UI); Phase 2 (cross-node reconciler + adaptive migration) spec'd and deferred.
2. **Enactment:** via the existing worker-agent API (controller sends desired state; agent runs local systemctl/install; controller node uses `local_worker` in-process). No SSH, no new per-node daemon.
3. **Migration cutover:** adaptive by capability — health-gated drain where the accelerator has headroom for both model loads; stop-source-first then start-target on single-NPU / single-GPU nodes; chosen from the capability map.
4. **Contract:** mandatory + CI-gated, with a one-time normalization of existing manifests and a short grandfather allowlist.

## Design

### A. The managed-service manifest contract

Service manifests already carry a `lifecycle:` block (`backend_type`, `default_url`, `auto_manage`, `start_cmd`, `stop_cmd`, `startup_timeout_seconds`). We EXTEND that block (do not invent a new one) with `unit`, `scope`, and `health`, and require it on host-managed backends:

```yaml
lifecycle:
  backend_type: rkllama
  default_url: http://localhost:7833
  auto_manage: true
  unit: rkllama.service        # systemd unit name (must match the installer's unit)
  scope: system                # system | user
  health:
    url: "http://localhost:7833/api/tags"
    expect: '"models"'         # substring assertion on a 200 body
  # start/stop/restart default to `systemctl <verb> <unit>`; keep start_cmd/stop_cmd only for non-systemd
  startup_timeout_seconds: 60
```

- The installer that provisions the backend MUST create a unit whose name/port match the manifest.
- **Current state (audit):** of the 10 `category: llm-runtime` service manifests, only rkllama sets `auto_manage: true`; llama-cpp/mlc-llm/openllm/rk-llama-cpp set `false`; exo/ezrknpu/litellm/ollama/vllm set none; NONE declare a `unit`.
- **Rule:** any `llm-runtime` service with `auto_manage: true` MUST declare `unit` + `scope` + `health`. A grandfather allowlist covers `auto_manage: false` / not-yet-migrated ones. Normalize the host-managed backends first (rkllama, qmd, rk-llama-cpp), which already ship real systemd units (`rkllama.service`, `qmd.service`, `rkllamacpp.service`).
- **CI:** a new `scripts/check_manifests.py managed-lint` (the existing `scripts/audit-manifests.py` is not run in CI) added as a step to `.github/workflows/doc-gate.yml`.

### B. Phase 1 — node-local Backend Service Manager (in the worker agent)

New module `cluster/backend_services.py` + worker-agent endpoints (HMAC-auth), mirrored in `local_worker`:

- `GET /worker/backends` → for each managed backend installed on this node: `{unit, enabled, active, health: {ok, detail}}`.
- `POST /worker/backends/{unit}/ensure` → idempotent: unit missing but backend installed → run the installer's systemd path / adopt an orphan (the installer `ExecStartPre` pkill reaps the bare process so the unit can bind the port); unit present → `enable --now`.
- `POST /worker/backends/{unit}/{start|stop|restart}` → systemctl verb, health-gated, fail-soft per unit (reuse the `_restart_ai_unit` scope-resolution + kill/reap-on-timeout logic, promoted into `backend_services`).

**Self-heal:** on worker-agent start and on each capability heartbeat, reconcile installed managed backends → enabled units. This would have auto-adopted the Pi's hand-started rkllama.

**#1743 rewire:** `restart_ai_stack` calls the node's `POST /worker/backends/{unit}/restart` (via `local_worker` for the controller node), so recovery works uniformly cluster-wide instead of assuming a local unit. `AI_STACK_UNITS` is derived from the node's managed manifests, not hardcoded.

### C. Cluster-app UI surface

**Read path:** each worker object in the existing `/api/cluster/workers` payload carries per-backend `{unit, enabled, active, health}` (sourced from the agent's `GET /worker/backends`, cached from the heartbeat) — the Cluster app already fetches `/api/cluster/workers`, so no new poll. **Action path:** a dedicated admin-gated `POST /api/cluster/workers/{node}/backends/{unit}/{start|stop|restart}` that the controller proxies to that node's worker agent (or `local_worker`). In `desktop/src/apps/ClusterApp.tsx`, each worker detail card gains a **Backend Services** subsection (replacing today's read-only `worker.backends` chips): per backend a name, a unit-state pill (enabled/active/failed), a health dot, and **admin-only** per-row Restart / Stop / Start actions plus a per-node "Restart AI Services" (the #1743 action, node-scoped).

### D. CI gate

A manifest-lint (extend `scripts/check_doc_gate.py` or a new `scripts/check_manifests.py`) fails any PR that adds/alters a backend service manifest without a valid `managed` block. Ships with a one-time normalization of current manifests and a grandfather allowlist. Unit-tested with valid/invalid/grandfathered fixtures.

### E. Phase 2 (spec, defer build) — cross-node reconciler

- Extend the capability/worker registry with `desired_backends` per node, fed by the placement `optimiser`.
- A controller reconcile loop diffs desired vs actual (from the `GET /worker/backends` heartbeat) and drives `ensure`/`stop` on the owning node's agent to converge.
- Migration cutover = adaptive (decision 3), extending `service_migrator` to host (non-LXC) backends: health-gated drain where headroom exists, else stop-source-first then start-target; roll back to source if the target never reports healthy within a timeout.

### F. Error handling

- Every action fails soft and reports per-unit: `not-installed`, `permission-denied` (polkit / interactive auth), `timeout` (kill + reap the child), `health-never-up`. Never a 500.
- Reconcile is idempotent and convergent; an unreachable node is skipped and retried on the next heartbeat, never blocking others.
- Migration rolls back to the source backend if the target does not report healthy within the cutover timeout.

### G. Testing

- **Manifest-lint:** unit tests over valid / invalid / grandfathered manifest fixtures.
- **Backend Service Manager:** unit tests with mocked `systemctl` (mirroring `tests/test_routes_system.py::TestRestartAiUnit`) covering ensure / start / stop / restart / health, adopt-orphan, and every fail-soft path.
- **Cluster UI:** component tests for the Backend Services rows (state pills, health, admin-gated actions), mocking the fetch.
- **Phase 2:** reconcile convergence tests against a fake worker agent (desired vs actual).
- **Acceptance:** on the Pi, adopt the hand-started rkllama under systemd via `ensure` and confirm it survives a simulated restart and is restartable from the Cluster UI.

## Units / boundaries (independently testable)

1. `managed` manifest schema + CI lint.
2. `cluster/backend_services.py` node backend-service manager + worker-agent endpoints (+ `local_worker`).
3. `#1743` rewire onto the node manager.
4. Cluster-app Backend Services UI + the `/api/cluster/workers` payload extension.
5. (Phase 2) controller reconcile loop + adaptive host-backend migration.

Phase 1 = units 1-4. Phase 2 = unit 5, its own spec/plan.

## Out of scope

- Non-systemd platforms (macOS/Windows launchd/services) — Phase 1 is systemd/Linux nodes; the contract's overridable start/stop leaves room for a later adapter.
- Model-weight placement/distribution (taOSnet) — separate epic; this manages the *service*, not the weights.
