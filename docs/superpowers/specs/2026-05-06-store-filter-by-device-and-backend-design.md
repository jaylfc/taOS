# Store Filter by Device and Backend — Design

**Date:** 2026-05-06
**Status:** Draft
**Author:** jaylfc

## Goal

When a user opens the **Models** tab of the Store, they should be able to filter the catalog by which device in their cluster will run the model and by which backend serves it. A flat list of every catalog model — most of which can't run on the user's hardware — is the current state and is hostile to discovery.

A secondary goal: install routing should follow the filter. Models live on the device that runs them; the filter is the natural place to express that intent.

## Non-Goals

- Filtering on Services / Agent-frameworks / MCPs / Plugins. Models-only for this iteration. The components are written so a single category guard extends them to Services later.
- Real-time worker capacity or backend-availability probing. The filter answers "could this run here?", not "is the backend installed and idle right now?".
- Custom user labels for cluster devices. Pills show whatever `friendly_name` the worker registered with.
- A "buying guide" mode for incompatible models. The IncompatibleToggle is the only discoverability surface for hardware not present.
- Hardware-tier model recommendations surfaced to the user. Manifests already encode `recommended` per tier; install logic uses it under the hood.

## Architecture

Two-tier hierarchical filter, all client-side. The catalog API returns the full set; the desktop app filters in memory.

```
┌─ left rail (existing categories) ─┬─ main pane ─────────────┐
│ All / Models* / Services / ...    │ search bar              │
│                                   │ ┌─ DevicePillBar ─────┐ │
│                                   │ └─────────────────────┘ │
│                                   │ ┌─ BackendPillBar ────┐ │
│                                   │ │ (only if device sel)│ │
│                                   │ └─────────────────────┘ │
│                                   │ <model grid>            │
│                                   │ <IncompatibleToggle>    │
└───────────────────────────────────┴─────────────────────────┘
```

Filter state lives in `StoreApp` — two `useState` arrays (`selectedDevices`, `selectedBackends`) — and is persisted to localStorage per `(user_id, profile_id)` so a reload preserves the user's choices. The pill bars only render when the active category is `models`.

## Components

### `DevicePillBar.tsx`
- Props: `devices: InstallTarget[]`, `selected: string[]`, `onChange: (names: string[]) => void`
- Horizontal scrollable strip of multi-select pills. Each pill shows `friendly_name` plus a small tier badge (e.g. `RK3588`, `M2 Pro`, `CPU only`).
- Empty selection = "All devices" (full catalog). Each pill toggles via `aria-pressed`. A "Clear" affordance appears once one or more are selected.
- Single-machine setups still render one pill (the controller) so the UX is consistent.

### `BackendPillBar.tsx`
- Props: `availableBackends: string[]`, `selected: string[]`, `onChange`, `disabled: boolean`
- Hidden entirely when `disabled` (no devices selected) — no flicker.
- `availableBackends` is the union of `variants[].backend` across all manifests where any selected device's `tier_id` appears in `hardware_tiers`. When devices change and a previously-selected backend is no longer available, it auto-deselects with a transient toast naming the dropped backend.
- **Auto-deselect is not auto-restore.** If `rkllama` is selected, the user deselects the Pi (so `rkllama` drops out of the available set and gets auto-deselected), and then later re-selects the Pi, `rkllama` does *not* come back automatically — it just becomes available for re-selection. This keeps the filter state predictable: deselection is always an explicit action by either the user or the auto-cleanup, never a hidden side effect of an unrelated change.
- Each pill renders via a `BACKEND_META` lookup (label, icon, color); unknown backends fall back to the raw string with default styling.

### `IncompatibleToggle.tsx`
- Hidden when zero models are excluded.
- Otherwise: small text button "Show N models that won't run on the selected devices".
- Expanded view: divider + dimmed grid of incompatible models, each card overlaid with a "Won't run here" chip. Tooltip explains which tiers the model needs.

### `StoreApp.tsx` integration
- Add the two pill bars and the toggle into the existing layout, gated on `activeCategory === "models"`.
- Pass `filteredApps` (compatible) and `incompatibleApps` from the new pure filter.
- Pass `selectedDevices` and `selectedBackends` down to `AppCard` so install actions can default to the right target (see Install Routing below).

### Mobile layout
- Both pill bars adopt the same `overflow-x-auto` horizontal-scroll treatment used by the existing category strip on mobile.
- Selected state persists across the strip's scroll position.
- IncompatibleToggle remains visible at the bottom of the grid; expanded incompatible cards scroll into view inline like the compatible grid.

## Data and APIs

### Manifest schema — no migrations required
Catalog manifests already carry the data we need:
- `variants[i].backend: string[]`
- `hardware_tiers: { [tier_id]: { recommended?: string, fallback?: string, ... } | "unsupported" }`

A one-off audit pass during implementation flags manifests where `backend` is missing/empty or `hardware_tiers` is empty so we can fix them upstream. The filter is forgiving: missing fields fall back to "no constraint", so unaudited manifests degrade gracefully rather than disappearing.

### `/api/cluster/install-targets` — extended

Today:
```json
[
  {"name": "local", "label": "This controller", "type": "local"},
  {"name": "orange-pi", "label": "orange-pi", "type": "remote", "addr": "..."}
]
```

After:
```json
[
  {
    "name": "local",
    "label": "This controller",
    "type": "local",
    "tier_id": "x86-cpu-only",
    "friendly_name": "Controller"
  },
  {
    "name": "orange-pi",
    "label": "orange-pi",
    "type": "remote",
    "addr": "https://192.168.6.123:8443",
    "tier_id": "arm-npu-16gb",
    "friendly_name": "orange-pi"
  }
]
```

`tier_id` for workers comes from the existing `_potential_capabilities()` helper used by `/api/cluster/workers`. For the controller row, we use `app.state.hardware_profile.profile_id`.

### `/api/store/catalog` — unchanged
Already returns `hardware_tiers`, `install`, and the variants array. No changes.

### Backend taxonomy — frontend constant
A small `desktop/src/apps/StoreApp/backends.ts` exports:
```ts
export const BACKEND_META: Record<string, {label: string; icon: string; color: string}> = {
  rkllama:        {label: "rkllama (NPU)",   icon: "🧠", color: "purple"},
  ollama:         {label: "Ollama",          icon: "🦙", color: "blue"},
  "llama-cpp":    {label: "llama.cpp",       icon: "🦫", color: "amber"},
  // one entry per backend that appears in the catalog
};
```
Adding a new backend requires one line here. Anything not in the map renders with default styling and the raw backend string as label.

## Filtering Logic

Pure function in `desktop/src/apps/StoreApp/filter.ts`:

```ts
export function filterModels(
  apps: CatalogApp[],
  selectedDevices: InstallTarget[],
  selectedBackends: string[],
): { compatible: CatalogApp[]; incompatible: CatalogApp[] } {
  const tiers = new Set(selectedDevices.map(d => d.tier_id).filter(Boolean));
  const backends = new Set(selectedBackends);

  const compatible: CatalogApp[] = [];
  const incompatible: CatalogApp[] = [];

  for (const app of apps) {
    const deviceOk =
      tiers.size === 0 ||
      [...tiers].some(tid => {
        const entry = app.hardware_tiers?.[tid];
        return entry !== undefined && entry !== "unsupported";
      });

    const appBackends = new Set(
      app.variants?.flatMap(v => v.backend ?? []) ??
      [app.install_method].filter(Boolean)
    );
    const backendOk =
      backends.size === 0 ||
      [...backends].some(b => appBackends.has(b));

    if (deviceOk && backendOk) compatible.push(app);
    else incompatible.push(app);
  }

  return { compatible, incompatible };
}
```

Semantics:
- `selectedDevices.length === 0` → all devices accepted (no filter).
- `selectedBackends.length === 0` → all backends accepted (no filter).
- Multi-select on either axis means union (any selected device matches; any selected backend matches).
- A model with empty `hardware_tiers` passes any device filter.
- A model with empty `variants[].backend` falls back to its top-level `install.method`. If both are absent, the model has no backend constraint and passes any backend filter.
- Hardware tier explicitly marked `"unsupported"` is treated as incompatible.

## Install Routing — Models Live Where They Run

The filter is also the natural way to express install intent. When the user clicks Install on a model card:

- **Exactly one device selected in the filter** → that device becomes the default install target. The model weight is downloaded onto that worker's filesystem (via the existing per-backend install handler), not the controller. Examples:
  - rkllama backend, Pi selected → weight lands at `~/rkllama/models/<model>/` on the Pi.
  - Ollama backend, Mac selected → triggers `ollama pull` on the Mac worker.
  - llama-cpp backend, controller selected → `.gguf` lands on the controller.
- **Zero or multiple devices selected** → the existing install-target dropdown on the model card stays as the explicit choice. Default selection of the dropdown is the first device whose `tier_id` is in the model's `hardware_tiers` (or the controller if none match).

The install-time backend handler is the layer that does the actual placement; it already takes a `target_remote` parameter for LXC installs. The change is to plumb the same `target_remote` through the model-install handlers (`store_install.py` default branch and per-backend handlers). Today the default branch writes to the controller-local `installed_apps` store and pulls files locally; that's what changes — the file pull and the `update_runtime_location` call need to target the resolved worker.

The persistence side already supports per-app runtime location (`InstalledAppsStore.update_runtime_location` writes `runtime_host` / `runtime_port`). This change just makes that field reflect the actual worker for model installs as it already does for LXC installs.

## Edge Cases

| Case | Behavior |
|---|---|
| No registered workers (controller-only) | Pill bar shows one pill ("Controller"). Filter still functional. |
| Worker offline | Pill renders with offline indicator dot, `aria-disabled` but clickable for planning. Install button on cards stays disabled (existing behavior). |
| Model with no `variants[].backend` | Falls back to `install_method`; if absent, has no backend constraint. |
| Model with empty `hardware_tiers` | Universally compatible — passes any device filter. |
| Model tier `"unsupported"` | Excluded; lands in the IncompatibleToggle group. |
| Selected device removed from cluster | Persisted filter is validated on Models-tab mount; missing devices are dropped before applying. |
| Both APIs (`install-targets`, `catalog`) fail | Pill bar hides, model grid falls back to the flat unfiltered list. No regression vs current behavior. |
| Loading state | DevicePillBar shows 3 skeleton pills until `install-targets` resolves. Catalog loading uses existing spinner. |

## Persistence

```
key:   taos.store.filter.{user_id}.{profile_id}
value: { devices: ["local", "orange-pi"], backends: ["rkllama"] }
```

Hydrate on Models-tab mount, then validate against current `install-targets` and `BACKEND_META`. Drop entries that no longer exist. Empty arrays are valid (= "All").

## Testing

### Unit — filter logic
`desktop/src/apps/StoreApp/filter.test.ts` covers:
- No devices → returns full list as `compatible`, empty `incompatible`.
- Single device → only models with matching `hardware_tiers` are compatible.
- Multi-device → union semantics (any device matches).
- Backend filter narrows further; intersection with device filter.
- Variant-less model falls back to `install_method`.
- Model with empty `hardware_tiers` passes device filter.
- Tier `"unsupported"` excluded.
- Combined: device match AND backend match required for compatibility.

About 12–15 cases against a static fixture. Vitest, fast, deterministic.

### Component — `BackendPillBar`
Snapshot test for pill rendering — known backend uses `BACKEND_META`; unknown backend falls back to raw string with default styling.

### Backend — `/api/cluster/install-targets`
Extend `tests/test_routes_cluster.py`:
- Controller-only response includes `tier_id` from `app.state.hardware_profile`.
- With one mocked remote registered, response includes both rows with their `tier_id`s.

### Manual smoke (post-merge, on the Pi)
1. Open Store → Models. See "Controller" + "orange-pi" pills.
2. Click "orange-pi" → backend bar reveals; rkllama, ollama, llama-cpp all pre-selected. Grid narrows.
3. Deselect ollama and llama-cpp → only `.rkllm`-format models remain.
4. Click "Show N incompatible" → dimmed cards appear with "Won't run here" chip.
5. Click Install on an rkllama-backed model with only the Pi selected → confirm the weight downloads to the Pi (`/home/jay/rkllama/models/<model>/`), not the controller.
6. Reload page → filter persists.
7. Stop the worker → pill is flagged offline; filter remains functional, install button on cards is disabled with tooltip.

No automated browser tests — the desktop UI doesn't have a Playwright harness yet and adding one is its own scope.

## Open Questions

None blocking. Two areas the spec deliberately defers:
- Multi-select install routing semantics (today: zero/multi → dropdown). If the right answer turns out to be "install on all selected devices", that's a follow-up change to `store_install.py` and the install button copy.
- A future Services-category extension is a one-line `if (activeCategory in {"models", "services"})` change, but Services have additional install nuance (LXC vs Docker, port routing) that probably wants its own brief design pass.
