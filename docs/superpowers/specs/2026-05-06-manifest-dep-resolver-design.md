# Manifest Dependency Resolver — Design Spec

**Status:** Draft for review
**Author:** jaylfc
**Date:** 2026-05-06
**Tracks:** PR-A (this spec) → PR-B (default-backend swap) → deferred work tracks

## Goal

Replace the implicit, brittle coupling between catalog manifests and install
backends with an explicit per-variant dependency model. Every model variant
declares which backends it can run on, against which target hardware, with
which RAM/VRAM/disk floors. A pure-function resolver picks a backend for any
given (manifest, variant, device), the install dispatcher chains backend +
model installs in one user click, and the same resolver feeds Store filtering
and green/amber/red compatibility borders.

## Motivation

Today the catalog has three overlapping ways of saying "this model runs on
this backend":

1. Top-level `install: {method: rkllama|rkllamacpp|download|...}` — pins one
   backend per manifest. The dispatcher branches on this string.
2. Variants advertise `backend: [ollama, llama-cpp]` — an advisory list,
   ignored by the dispatcher.
3. Top-level `hardware_tiers: {arm-npu-16gb: full, ...}` — used for filtering
   in the Store and for picking a recommended variant.

This works for single-backend single-target models but breaks down as soon as
we want:

- A GGUF runnable on three different backends with different RAM floors per
  backend.
- New architectures (Gemma 4, Qwen 3.5+) that aren't supported by `rkllama`
  but *are* supported by `rk-llama.cpp` — the dispatcher can't pick between
  them based on what's available on the target device.
- Auto-installing the right backend when a user clicks Install on a model and
  the backend isn't on the worker yet.
- Honest per-device compatibility classification (green/amber/red) for Store
  cards.
- **Reliable filtering** in the Store. The current Store filter (PR #319)
  reports "no compatible models" for an Orange Pi 5 Plus, despite the Pi
  having ~14 rkllm Qwen manifests in the catalog that should be marked
  compatible. The filter mixes `hardware_tiers` strings (`arm-npu-16gb`)
  with cluster-reported tier strings, and the matching is fragile across
  manifests that use slightly different tier vocabulary or omit the field
  entirely. Concrete `targets` derived from actual capability detection
  remove this whole class of mismatch.

A unified `requires.backends` block per variant + a real resolver fixes all of
the above.

## Non-goals

- Multi-model concurrent serving on a single backend instance — the resolver
  answers "can this device ever run this model?" not "can this run alongside
  what's loaded right now?" Concurrency lives in the lifecycle manager.
- Live free-RAM gating — the resolver uses **total** RAM/VRAM (capacity), not
  current free, because dynamic unload makes free-RAM-now an unreliable
  signal. (Disk is still "free" — disk doesn't auto-evict.)
- Lifecycle promotion of archived files when hardware joins the cluster —
  `force=True` is the resolver's contract; the actual archive→worker move is
  a separate work track.
- Default-backend swap (Pi-NPU defaults to rk-llama.cpp instead of rkllama) —
  deferred to PR-B once the resolver makes lazy-install of rkllama trivial.
- Help/Guides app, Store color borders, hardware-tier templates, Pack 2-12
  manifests — separate tracks; this PR makes them possible.

## Architecture overview

```
┌────────────────────┐       ┌────────────────────┐       ┌──────────────────┐
│ catalog manifest    │       │ resolver            │       │ install          │
│ (model)             │──────▶│ (pure function)     │──────▶│ dispatcher       │
│ requires.backends:  │       │ resolve(m, v, d, f) │       │ chains backend  │
│ context_window:     │       │ classify(m, d)      │       │ + model install │
└────────────────────┘       └────────────────────┘       └──────────────────┘
        ▲                              ▲                            │
        │                              │                            ▼
┌────────────────────┐       ┌────────────────────┐       ┌──────────────────┐
│ catalog manifest    │       │ device capability   │       │ existing         │
│ (backend service)   │       │ snapshot            │       │ Installer classes│
│ install: {method}   │       │ targets / RAM /     │       │ (one per         │
└────────────────────┘       │ disk / installed    │       │  backend ID)     │
                              │ backend service IDs │       └──────────────────┘
                              └────────────────────┘
```

## Schema

### Model manifest changes

**Added** to every model manifest:

- Top-level `context_window: <int>` — model's max context length in tokens.
  Architecture-level, not per-variant. Sourced from the upstream HuggingFace
  `config.json` (`max_position_embeddings`) or model card during the migration
  audit pass.
- Per-variant `requires.backends: [{id, targets, min_ram_mb, min_vram_mb?}]` —
  ordered list of backend candidates that can run this variant.

**Removed** from every model manifest:

- Top-level `install: {method: ...}` — replaced by `requires.backends`.
- `variants[].backend: [...]` — replaced by `requires.backends[].id`.

**Kept** (opaque to dispatcher, consumed only by future Help app):

- Top-level `hardware_tiers: {<tier>: full|degraded|recommended}` —
  curator-opinion metadata about device class fit. Dispatcher does not use it.

### Backend service manifest

Backend service manifests (already exist in `app-catalog/services/` for
rkllama, rk-llama-cpp, etc.) **do not change**. They keep their existing
`install: {method: pip|docker|lxc|download|rkllamacpp|...}` block — that's
how the dispatcher installs the backend itself when chaining.

The constraint added by this PR: backend service manifests **must not** declare
`requires.backends` themselves. Backends are leaves in the dependency graph.
Enforced by the audit script.

### Targets enum (catalog-wide)

Initial set:

- `rockchip-rk3588` — Rockchip RK3588 NPU (Orange Pi 5 Plus, friends)
- `apple-silicon` — Apple Silicon (M1/M2/M3+) for MLX / Metal backends
- `x86-cuda` — x86_64 with NVIDIA CUDA-capable GPU
- `x86-vulkan` — x86_64 with Vulkan-capable GPU (AMD, Intel Arc, NVIDIA without CUDA)
- `arm-vulkan` — ARM with Vulkan-capable GPU (Mali, Adreno, NVIDIA Jetson)
- `cpu` — generic CPU fallback (any arch)

Targets can be added later (e.g. `arm-mali`, `xpu` for Intel, `rocm` for AMD).
Each device's targets list is reported by the cluster module per worker.

### Concrete example

```yaml
id: qwen2.5-3b
name: Qwen 2.5 3B Instruct
type: model
version: 2.5.0
description: "Compact 3B with tool calling"
homepage: https://huggingface.co/Qwen/Qwen2.5-3B-Instruct
license: "Qwen Research License"
capabilities: [chat, tool-calling, code]
context_window: 32768

variants:
  - id: q4_k_m
    name: "Q4_K_M (1.9GB)"
    format: gguf
    size_mb: 1900
    download_url: https://huggingface.co/.../Qwen2.5-3B-Instruct-Q4_K_M.gguf
    requires:
      backends:
        - id: rk-llama-cpp
          targets: [rockchip-rk3588]
          min_ram_mb: 4096
        - id: ollama
          targets: [apple-silicon, x86-cuda, cpu]
          min_ram_mb: 4096
          min_vram_mb: 6144
        - id: llama-cpp
          targets: [cpu]
          min_ram_mb: 6144

  - id: q8_0
    name: "Q8_0 (3.4GB)"
    format: gguf
    size_mb: 3400
    download_url: https://huggingface.co/.../Qwen2.5-3B-Instruct-Q8_0.gguf
    requires:
      backends:
        - id: rk-llama-cpp
          targets: [rockchip-rk3588]
          min_ram_mb: 6144
        - id: ollama
          targets: [apple-silicon, x86-cuda, cpu]
          min_ram_mb: 6144

# Opaque metadata for the future Help app — dispatcher ignores this.
hardware_tiers:
  arm-npu-16gb: {recommended: q4_k_m}
  arm-npu-32gb: {recommended: q8_0}
  x86-cuda-12gb: {recommended: q8_0}
  apple-silicon: {recommended: q8_0}
  cpu-only: {recommended: q4_k_m}
```

## Resolver

### Module location

`tinyagentos/catalog/resolver.py` — new module. Pure functions, no I/O,
no httpx, no cluster lookups. Imports allowed: typing, dataclasses, the
manifest types module.

### Public API

```python
@dataclass(frozen=True)
class DeviceCapability:
    """Device capacity snapshot — supplied by the caller."""
    device_id: str
    targets: tuple[str, ...]            # e.g. ("rockchip-rk3588", "cpu")
    total_ram_mb: int                   # capacity, not current free
    total_vram_mb: int                  # 0 if no GPU
    free_disk_mb: int                   # disk IS "free" — no auto-evict
    installed_backends: tuple[str, ...] # service IDs known installed

@dataclass(frozen=True)
class BackendDep:
    id: str
    targets: tuple[str, ...]
    min_ram_mb: int
    min_vram_mb: int = 0

@dataclass(frozen=True)
class ResolveOk:
    backend_id: str
    variant_id: str
    action: Literal["use", "install_chain"]

@dataclass(frozen=True)
class ResolveErr:
    reason: str                          # human-readable summary
    near_miss: dict                      # {variant, blocked_by, short_by_mb}
    suggestions: list[str]               # actionable advice strings

ResolveResult = Union[ResolveOk, ResolveErr]


def resolve(
    manifest: ModelManifest,
    variant_id: str | Literal["auto"],
    device: DeviceCapability,
    *,
    force: bool = False,
) -> ResolveResult: ...


def classify(
    manifest: ModelManifest,
    device: DeviceCapability,
) -> Literal["green", "amber", "red"]: ...
```

### Algorithm — `resolve`

1. **Variant selection.** If `variant_id == "auto"`, sort `manifest.variants`
   by `size_mb` **descending** and iterate. Otherwise use the named variant
   only.
2. **For each candidate variant**, walk `variant.requires.backends` in
   declaration order. For each backend dep entry, check gates:
   - `entry.targets ∩ device.targets == ∅` → reject (no hardware path).
     **Bypassed when `force=True`.**
   - `device.total_ram_mb < entry.min_ram_mb` → reject (RAM short).
     **Bypassed when `force=True`.**
   - `entry.min_vram_mb > 0` and `device.total_vram_mb < entry.min_vram_mb` →
     reject (VRAM short). **Bypassed when `force=True`.**
   - `device.free_disk_mb < variant.size_mb` → reject (disk short).
     **Never bypassed** — you can't download a file with no disk space.
3. **First entry passing all gates wins.** Return `ResolveOk(action="use")`
   if `entry.id ∈ device.installed_backends`, else
   `ResolveOk(action="install_chain")`. The chosen variant is recorded in
   `ResolveOk.variant_id`.
4. **No candidate resolved.** Return `ResolveErr` with:
   - `reason`: terse one-line summary of the closest miss
   - `near_miss`: `{variant: <id>, blocked_by: "ram"|"vram"|"disk"|"target", short_by_mb: <int>}`
   - `suggestions`: advice strings the UI can show as buttons or list items
     (e.g. "Pick a smaller variant", "Install on workerB", "Free up disk")

### Algorithm — `classify`

Calls `resolve(manifest, "auto", device, force=False)` and inspects the result:

- `green` if `ResolveOk` and the winning backend's targets list contains any
  non-`cpu` target.
- `amber` if `ResolveOk` and the only winning target is `cpu`.
- `red` otherwise.

This is what feeds the future Store card border colour. Shipping the function
in PR-A; the Store UI consumption is a deferred work track.

### Pure-function discipline

The resolver lives client-side too. The Store frontend calls a JSON endpoint
(`POST /api/store/resolve` — added in PR-A) that wraps the same Python
function. The frontend mirrors of `BackendDep` etc. live in
`desktop/src/apps/StoreApp/types.ts`. Server is the single source of truth;
client mirrors stay thin.

## Install dispatcher

### File

`tinyagentos/routes/store_install.py` — extended, not replaced. The existing
endpoint `/api/store/install-v2` keeps its URL and request shape; the body of
the dispatch function is rewritten to use the resolver.

### Flow

```
POST /api/store/install-v2
  body: {manifest_id, variant_id|"auto", target_remote|None, force?: false}

1. Load manifest from catalog (fail fast with 404 if missing).
2. Get capability snapshot for target device:
     - Local install → snapshot from local hardware module.
     - Remote → query worker via existing /api/cluster/workers/<id>/capacity.
3. resolve(manifest, variant_id, device, force=force) → result
4. Branch on result:
     ResolveErr(...)           → 422 with structured body (reason / near_miss /
                                  suggestions). No state changes.
     ResolveOk(action="use")   → goto step 6.
     ResolveOk(action="install_chain") → goto step 5.
5. Install the backend service first:
     a. Load app-catalog/services/<backend_id>/manifest.yaml
        (assert no requires.backends — backend service manifests are leaves)
     b. Recursively call dispatch(backend_manifest, "auto", target_remote)
        Recursion depth is bounded at 1; backend installs use their existing
        install.method paths (pip / docker / download / rkllamacpp / lxc).
     c. On failure → 500 with chain failure detail. Do NOT proceed to model.
     d. On success → register backend as installed on device, refresh
        capability snapshot (installed_backends now includes backend_id).
6. Install the model:
     a. installer = get_installer(backend_id, ...)
     b. result = await installer.install(
          app_id=manifest_id,
          install_config=manifest.install_config_for_backend(backend_id),
          variant=resolved_variant,
          ...
        )
     c. On failure → 500 with installer error.
     d. On success → persist runtime_location to registry.
7. Return 200:
     {chain: [{step: "backend", id: <backend_id>, status: "installed"|"reused"},
              {step: "model", id: <manifest_id>, status: "installed",
               runtime_location: ...}]}
```

### Idempotency

If a previous attempt installed the backend but failed on the model:
- Re-running the request causes `resolve` to see `installed_backends` includes
  the backend → returns `action="use"` → step 5 is skipped → step 6 runs.

If the backend itself partially installed (e.g., binary on disk but service
not enabled), the backend's installer is responsible for detecting and
recovering. Out of scope for this resolver.

### Force flag downstream

When `force=True` and resolver returns `ResolveOk(...)` despite RAM/VRAM/target
mismatch, the chain proceeds normally. The model is installed into whichever
backend matched (which may be a backend the device can't actually run).

When `force=True` but resolver still returns `ResolveErr` because there's no
backend match at all (e.g., MLX-only model on Pi-only cluster), the
dispatcher writes the file to `~/taos/archive/models/<manifest_id>/<variant_id>/`
and registers it with `archived: true`. No backend install runs.

This archive lifecycle (move file to worker when compatible hardware appears)
is **out of scope** for this PR. The registry entry exists; promoting it is
a future work track.

## Migration

### Migration script

`scripts/migrate-manifests-to-requires-backends.py` — one-shot, deleted
after PR-A merges.

For each model manifest:
1. Read existing `install.method` and `variants[].backend`.
2. Map to `requires.backends` using the table below.
3. Look up `context_window` from the cached HuggingFace `config.json` (script
   downloads it once per model into `.cache/migration/`).
4. Write back: add `requires.backends` per variant + `context_window`,
   remove `install.method` + `variants[].backend`. Preserve all other fields
   and YAML formatting (uses `ruamel.yaml` round-trip).
5. Print a summary of what changed and what needed manual review.

### Mapping table

| Old `install.method` | Old `variants[].backend` | New `requires.backends`                                                                          |
| -------------------- | ------------------------ | ----------------------------------------------------------------------------------------------- |
| `rkllama`            | (any)                    | `[{id: rkllama, targets: [rockchip-rk3588], min_ram_mb: <variant.min_ram_mb>}]`                  |
| `rkllamacpp`         | (any)                    | `[{id: rk-llama-cpp, targets: [rockchip-rk3588], min_ram_mb: <variant.min_ram_mb>}]`             |
| `download`           | `[ollama, llama-cpp]`    | `[{id: ollama, targets: [apple-silicon, x86-cuda, cpu], ...}, {id: llama-cpp, targets: [cpu], ...}]` |
| `download`           | `[mlx]`                  | `[{id: mlx, targets: [apple-silicon], ...}]`                                                    |
| `download`           | `[comfyui]`              | `[{id: comfyui, targets: [x86-cuda, x86-vulkan], ...}]`                                         |

The script does best-effort inference; the **manual audit pass** catches
edge cases.

### Manual audit pass

Done after the migration script runs, documented as a checklist on the PR:

- [ ] Every model manifest has `context_window` set to a non-zero value.
- [ ] Every model manifest has `variants[].requires.backends` (at least one
      entry per variant).
- [ ] Multimodal manifests (vision-language, audio-language) point at the
      right backend (e.g. ComfyUI for SD pipelines, llama.cpp with
      `--mmproj` for vision-LLMs).
- [ ] Quant variants with backend-specific quirks have correct backend lists
      (e.g. AWQ quants only run on `vllm` / `tensorrt-llm`, not Ollama).
- [ ] Each `min_ram_mb` is sane: no smaller than `variant.size_mb * 1.2`
      for fp16/bf16, `variant.size_mb * 1.1` for quantized formats.
- [ ] Each `targets[]` value is in the catalog-wide enum.

The audit checklist is mechanical enough for one reviewer to spot-check 5-6
manifests across categories.

### Audit script

`scripts/audit-manifests.py` — already exists, extended in PR-A:

1. Every model manifest declares `variants[].requires.backends` and
   `context_window`.
2. No model manifest has the deprecated `install.method` or
   `variants[].backend` fields. CI fails if found.
3. Every backend ID referenced exists as a service manifest.
4. Backend service manifests do not declare `requires.backends` themselves.
5. Every `targets[]` value is in the catalog-wide enum.
6. Every `min_ram_mb` is non-zero.

CI runs the audit script; failure blocks merge.

## Test plan

### Unit tests (resolver — pure)

`tests/catalog/test_resolver.py`:

- `resolve` returns `ResolveOk("use")` when device has installed backend that
  matches one of the variant's deps.
- `resolve` returns `ResolveOk("install_chain")` when device has the hardware
  but not the backend installed.
- `resolve` returns `ResolveErr` when no target intersection.
- `resolve` returns `ResolveErr` when total_ram_mb is below floor.
- `resolve` returns `ResolveErr` when total_vram_mb is below floor (and a
  vram-requiring backend was the only match).
- `resolve` returns `ResolveErr` when free_disk_mb < variant.size_mb (even
  when other gates pass).
- `resolve` with `force=True` bypasses target/RAM/VRAM gates but not disk.
- `resolve` with `variant_id="auto"` picks the largest fitting variant
  (verifies size_mb-descending iteration).
- `resolve` declaration-order tiebreaker: when two backend deps both pass,
  the first one declared wins.
- `resolve` returns the closest near-miss for the error UI.
- `classify` returns "green" when winner has non-cpu target.
- `classify` returns "amber" when only `cpu` targets resolve.
- `classify` returns "red" when nothing resolves.

### Unit tests (audit script)

`tests/scripts/test_audit_manifests.py`:

- A manifest missing `context_window` fails audit.
- A manifest with deprecated `install.method` fails audit.
- A backend service manifest with `requires.backends` fails audit.
- A reference to a non-existent backend ID fails audit.
- A target outside the enum fails audit.
- A clean catalog passes audit.

### Integration test (dispatcher)

`tests/routes/test_store_install_v2.py`:

- `install_chain` flow: backend uninstalled → endpoint installs backend →
  endpoint installs model. Asserts both registry rows updated.
- `install_chain` recovery: backend install fails → 500 returned, model not
  installed, registry has only the failed-backend row.
- Idempotent re-run: backend already installed → endpoint returns chain with
  `step: backend, status: reused`.
- `force=true` archive path: model with no backend match on this cluster →
  file written under archive dir, registry has `archived: true`.

### Mac/Pi/CI mix

- Resolver tests run on every CI matrix run (pure Python, fast).
- Dispatcher integration tests run on Linux CI (Python 3.10/3.11/3.12/3.13).
- The recursive-backend chain test mocks the actual installers to avoid
  needing real `npm`, `docker`, etc. inside CI containers.

### E2E smoke on Pi (canonical happy-path)

After the PR is merged and pulled to the Pi, manual run:

1. Open Store on Pi, navigate to Apps → installed → uninstall `rk-llama.cpp`.
   Verify systemd unit is stopped and disabled, model files moved or removed.
2. Open Store, Models tab, click Install on a Qwen GGUF that lists
   `rk-llama-cpp` as its first backend (e.g., `qwen2.5-3b` Q4_K_M).
3. Confirm-then-chain dialog appears: "This needs rk-llama.cpp on this device.
   Install both? (12 MB + 1.9 GB)" — click "Install Both".
4. Watch the chain: rk-llama.cpp downloads from HF mirror tarball, systemd
   unit installed and enabled. Resolver re-resolves, action flips to
   `use`. Qwen GGUF downloads to `~/rk-llama.cpp/models/`, active.gguf
   symlink updated, llama-server restarts on port 8090.
5. Verify `/health` returns 200, model is callable via OpenAI-compatible
   `/v1/chat/completions`.
6. Open the Chat app, confirm the new model appears in the agent picker
   and a basic message round-trips.

### Out of scope for the test plan

- Color-coded Store borders — `classify` is shipped and tested but the UI
  consumption is a separate work track.
- Archive-anyway lifecycle promotion — `force=True` is shipped and tested
  but the move-to-worker flow is a separate work track.
- Mass migration of every Pack 2-12 manifest — those PRs each ship with
  their manifests already in the new shape and pass the audit script.

## Sequencing

### PR-A (this spec → plan → implement)

- Schema migration (~40 manifests + audit extension)
- New resolver module + tests
- Recursive install dispatcher + tests
- New `/api/store/resolve` endpoint for client-side compatibility checks
- E2E smoke test verified on Pi

### PR-B (next branch, opens after PR-A is merged and Pi-verified)

- Default-backend swap on Pi-NPU: `scripts/install-server.sh` and the
  setup wizard install `rk-llama.cpp` by default; `rkllama` becomes lazy
  (only installed when user clicks an `.rkllm` model).
- Updates the rkllama service manifest's first-install behavior.

### Deferred (separate work tracks; captured in memory)

- Green/amber/red Store card borders (uses `classify`).
- Hardware-tier install templates / setup wizard.
- Help / Guides app for function-based recommendations.
- Archive-anyway lifecycle promotion.
- ComfyUI / vision-language / multimodal-specific backend authoring
  (Pack 2-12 — each pack PR ships with its manifests in the new shape).

## Open questions / explicit decisions

These were resolved during brainstorming and are recorded for future readers:

1. **Hard cutover vs additive migration:** chose hard cutover. Two-schema
   purgatory always becomes permanent. ~40 manifests is one afternoon.
2. **Per-variant or per-manifest backend list:** per-variant. Quant variants
   genuinely behave differently across backends (real-world experience).
3. **Keep `hardware_tiers`:** kept as opaque metadata for the future Help
   app; dispatcher does not consume it.
4. **Tiebreaker when multiple backends match:** manifest-author declaration
   order. Author has the curator opinion.
5. **Confirm-then-chain vs auto-install:** confirm-then-chain. Honest about
   download size; one extra click is worth it.
6. **Total RAM vs free RAM in resolver:** total. Dynamic unload makes
   free-now an unreliable signal. Disk stays "free" because it doesn't
   auto-evict.
7. **Auto-variant order:** quality-first (largest fitting variant), not
   smallest-fit. User can override with explicit variant ID.
8. **Backend service manifests as runtime entities vs new type:** reuse
   existing service type. Services and runtimes share enough mechanism that
   a new entity is conceptual cleanliness without operational benefit.
9. **`context_window` placement:** model top-level, not per-variant.
   Quantization doesn't change architectural ctx limit.

## File-by-file changes (preview for the implementation plan)

```
# New
tinyagentos/catalog/resolver.py
tinyagentos/catalog/__init__.py (if not present)
tests/catalog/test_resolver.py
scripts/migrate-manifests-to-requires-backends.py (deleted in cleanup)
desktop/src/apps/StoreApp/resolver-types.ts (frontend mirrors)

# Modified
tinyagentos/routes/store_install.py     (rewrite dispatch using resolver)
tinyagentos/routes/store.py              (add /api/store/resolve endpoint)
tinyagentos/installers/base.py           (no behavior change; possibly add
                                          backend_id-keyed dispatch)
scripts/audit-manifests.py               (extend with new rules)
app-catalog/models/*/manifest.yaml       (~30 model manifests migrated)
docs/catalog-platform-status.md          (note schema change)
tests/routes/test_store_install_v2.py    (new tests)
tests/scripts/test_audit_manifests.py    (new tests)

# No change
app-catalog/services/*/manifest.yaml     (kept as-is; install.method stays)
tinyagentos/installers/{pip,docker,download,lxc,rkllama,rkllamacpp}_installer.py
                                          (existing installers reused)
```

## Success criteria

- All tests pass on CI matrix (3.10–3.13).
- Audit script passes on the migrated catalog.
- E2E Pi smoke test passes (uninstall rk-llama.cpp → install Qwen via
  resolver → chat works).
- No model manifest has the deprecated fields after the migration commit.
- Resolver function is callable from both server and client; classify output
  matches expected green/amber/red on at least one curated manifest set
  (test fixtures).
- **Store filter regression-fixed:** opening the Store on the Orange Pi 5
  Plus shows the ~14 rkllm Qwen manifests as compatible with `green` borders
  (rk-llama-cpp / rkllama against `rockchip-rk3588`). The "no compatible
  models" empty-state from PR #319 must not reproduce. Verified manually as
  part of the E2E smoke test.
