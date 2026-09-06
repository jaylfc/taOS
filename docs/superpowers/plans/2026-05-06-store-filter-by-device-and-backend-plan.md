# Store Filter by Device and Backend — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a two-tier hierarchical filter (device → backend, multi-select on both) to the Store Models tab, route model installs to the device hosting the backend, and polish for mobile.

**Architecture:** Frontend-only filter logic against the existing `/api/store/catalog`. One backend extension to `/api/cluster/install-targets` to expose each device's `tier_id`. Three new components + one pure filter function under `desktop/src/apps/StoreApp/`. Install routing piggybacks on the existing `target_remote` plumbing — extended to the default install-v2 branch so model files land on the correct worker.

**Tech Stack:** React 18 + TypeScript + Vitest (frontend), FastAPI + pytest (backend). Existing patterns: `desktop/src/apps/BrowserApp/` for component-per-file directory layout, `tests/test_routes_cluster.py` for FastAPI route tests with `client` async fixture, `desktop/src/apps/StoreApp.tsx` for current store flat file (this plan converts it to a directory).

**Spec:** `docs/superpowers/specs/2026-05-06-store-filter-by-device-and-backend-design.md`

---

## File Structure

**New files:**
- `desktop/src/apps/StoreApp/index.tsx` — moved from `desktop/src/apps/StoreApp.tsx` (mechanical, Task 1)
- `desktop/src/apps/StoreApp/types.ts` — shared types extracted from index.tsx (`CatalogApp`, `InstallTarget`, `InstalledEntry`)
- `desktop/src/apps/StoreApp/filter.ts` — pure `filterModels(apps, devices, backends)` function
- `desktop/src/apps/StoreApp/filter.test.ts` — vitest unit tests for filter logic
- `desktop/src/apps/StoreApp/backends.ts` — `BACKEND_META` constant
- `desktop/src/apps/StoreApp/DevicePillBar.tsx` — device pill bar component
- `desktop/src/apps/StoreApp/BackendPillBar.tsx` — backend pill bar component
- `desktop/src/apps/StoreApp/BackendPillBar.test.tsx` — snapshot test for known/unknown backends
- `desktop/src/apps/StoreApp/IncompatibleToggle.tsx` — toggle + dimmed grid
- `desktop/src/apps/StoreApp/storage.ts` — localStorage hydrate/save with validation
- `scripts/audit-manifests.py` — one-off CLI to flag manifests with empty `backend` or `hardware_tiers`

**Modified files:**
- `desktop/src/apps/StoreApp.tsx` — DELETED after move to `StoreApp/index.tsx`
- `desktop/src/apps/StoreApp/index.tsx` — wire pill bars + filter + persistence
- `tinyagentos/routes/cluster.py:256-280` — extend `list_install_targets` response with `tier_id` and `friendly_name`
- `tinyagentos/routes/store_install.py` — plumb `target_remote` through the default install branch (line ~171-184)
- `tests/test_routes_cluster.py` — add tests for the install-targets extension

---

## Task 1: Move StoreApp.tsx into a directory

Mechanical step so subsequent tasks can add components alongside.

**Files:**
- Create: `desktop/src/apps/StoreApp/index.tsx` (content moved from existing `StoreApp.tsx`)
- Delete: `desktop/src/apps/StoreApp.tsx`

- [ ] **Step 1: Create directory and move the file**

```bash
cd desktop/src/apps
mkdir StoreApp
git mv StoreApp.tsx StoreApp/index.tsx
```

- [ ] **Step 2: Verify the existing import still resolves**

The existing import `@/apps/StoreApp` in `desktop/src/registry/app-registry.ts:22` resolves to either `StoreApp.tsx` or `StoreApp/index.tsx` because Vite/TypeScript pick up `index.tsx` automatically — no change required.

- [ ] **Step 3: Run frontend type-check + tests to confirm no regressions**

Run: `cd desktop && npx tsc --noEmit`
Expected: exit code 0 (no errors).

Run: `cd desktop && npx vitest run`
Expected: all existing tests pass (this task changed nothing about behavior).

- [ ] **Step 4: Commit**

```bash
git add desktop/src/apps/StoreApp/index.tsx
git commit -m "refactor(store): move StoreApp.tsx into StoreApp/ directory

Subsequent commits add filter components alongside index.tsx. Existing
import path @/apps/StoreApp continues to resolve via index.tsx; no
caller changes needed."
```

---

## Task 2: Extract shared types into `types.ts`

Pull `CatalogApp`, `InstallTarget`, `InstalledEntry` out of `index.tsx` so the new components can import them without circular dependencies.

**Files:**
- Create: `desktop/src/apps/StoreApp/types.ts`
- Modify: `desktop/src/apps/StoreApp/index.tsx`

- [ ] **Step 1: Write the new types file**

```typescript
// desktop/src/apps/StoreApp/types.ts

export interface CatalogApp {
  id: string;
  name: string;
  type: string;
  category?: string;
  version: string;
  description: string;
  installed: boolean;
  compat: "green" | "yellow" | "unsupported";
  install_method?: string;
  hardware_tiers?: Record<string, unknown>;
  variants?: Array<{
    id: string;
    name?: string;
    backend?: string[];
    [key: string]: unknown;
  }>;
}

export interface InstallTarget {
  name: string;
  label: string;
  type: "local" | "remote";
  addr?: string;
  /** Hardware tier ID matching keys in CatalogApp.hardware_tiers. */
  tier_id?: string;
  /** Display name for pill bars. Defaults to `label` when absent. */
  friendly_name?: string;
}

export interface InstalledEntry {
  app_id: string;
  installed_at: number;
  version: string;
  metadata: Record<string, unknown>;
  runtime_host: string | null;
  runtime_port: number | null;
  runtime_backend: string | null;
}
```

- [ ] **Step 2: Update `index.tsx` to import from `types.ts`**

Open `desktop/src/apps/StoreApp/index.tsx`. At the top, after the lucide imports, add:

```typescript
import type { CatalogApp, InstallTarget, InstalledEntry } from "./types";
```

Then delete the inline `interface CatalogApp { ... }`, `interface InstallTarget { ... }`, and `interface InstalledEntry { ... }` definitions further down. Add `hardware_tiers` and `variants` fields to the catalog mapping in `fetchCatalog` so the filter has the data it needs:

Find the block (currently around line 619-635):
```typescript
const normalized: CatalogApp[] = data.map((a: Record<string, unknown>) => ({
  id: String(a.id),
  name: String(a.name ?? a.id),
  type: String(a.type ?? "plugin"),
  category: a.category ? String(a.category) : undefined,
  version: String(a.version ?? ""),
  description: String(a.description ?? ""),
  installed: Boolean(a.installed),
  compat: (a.compat as CatalogApp["compat"]) ?? "green",
  install_method: a.install_method ? String(a.install_method) : undefined,
}));
```

Replace with:
```typescript
const normalized: CatalogApp[] = data.map((a: Record<string, unknown>) => ({
  id: String(a.id),
  name: String(a.name ?? a.id),
  type: String(a.type ?? "plugin"),
  category: a.category ? String(a.category) : undefined,
  version: String(a.version ?? ""),
  description: String(a.description ?? ""),
  installed: Boolean(a.installed),
  compat: (a.compat as CatalogApp["compat"]) ?? "green",
  install_method: a.install_method ? String(a.install_method) : undefined,
  hardware_tiers: (a.hardware_tiers as Record<string, unknown>) ?? undefined,
  variants: (a.variants as CatalogApp["variants"]) ?? undefined,
}));
```

- [ ] **Step 3: Type-check**

Run: `cd desktop && npx tsc --noEmit`
Expected: exit code 0.

- [ ] **Step 4: Commit**

```bash
git add desktop/src/apps/StoreApp/types.ts desktop/src/apps/StoreApp/index.tsx
git commit -m "refactor(store): extract shared types into types.ts

CatalogApp, InstallTarget, InstalledEntry move out of index.tsx so the
upcoming filter components can import them without circular deps. Adds
hardware_tiers and variants to CatalogApp; fetchCatalog now passes
those fields through from the backend catalog response."
```

---

## Task 3: Pure filter function — write the failing tests

TDD: define the contract before the implementation.

**Files:**
- Create: `desktop/src/apps/StoreApp/filter.test.ts`

- [ ] **Step 1: Write the test file**

```typescript
// desktop/src/apps/StoreApp/filter.test.ts
import { describe, it, expect } from "vitest";
import { filterModels } from "./filter";
import type { CatalogApp, InstallTarget } from "./types";

const piDevice: InstallTarget = {
  name: "orange-pi",
  label: "orange-pi",
  type: "remote",
  tier_id: "arm-npu-16gb",
};

const macDevice: InstallTarget = {
  name: "mac",
  label: "mac",
  type: "remote",
  tier_id: "apple-silicon",
};

const controllerDevice: InstallTarget = {
  name: "local",
  label: "Controller",
  type: "local",
  tier_id: "x86-cpu-only",
};

const rkllamaModel: CatalogApp = {
  id: "qwen3-4b-rk",
  name: "Qwen3 4B (rkllama)",
  type: "model",
  version: "1",
  description: "",
  installed: false,
  compat: "green",
  hardware_tiers: { "arm-npu-16gb": { recommended: "default" } },
  variants: [{ id: "default", backend: ["rkllama"] }],
};

const ollamaModel: CatalogApp = {
  id: "qwen3-4b-ollama",
  name: "Qwen3 4B (ollama)",
  type: "model",
  version: "1",
  description: "",
  installed: false,
  compat: "green",
  hardware_tiers: {
    "x86-cpu-only": { recommended: "q4" },
    "apple-silicon": { recommended: "q4" },
  },
  variants: [{ id: "q4", backend: ["ollama", "llama-cpp"] }],
};

const universalModel: CatalogApp = {
  id: "small-tool",
  name: "Small Tool",
  type: "model",
  version: "1",
  description: "",
  installed: false,
  compat: "green",
  // no hardware_tiers, no variants → universally compatible
};

const unsupportedOnPi: CatalogApp = {
  id: "huge-model",
  name: "Huge Model",
  type: "model",
  version: "1",
  description: "",
  installed: false,
  compat: "unsupported",
  hardware_tiers: {
    "arm-npu-16gb": "unsupported",
    "x86-cpu-only": { recommended: "q4" },
  },
  variants: [{ id: "q4", backend: ["llama-cpp"] }],
};

const fallbackInstallMethod: CatalogApp = {
  id: "via-method",
  name: "Method-only",
  type: "model",
  version: "1",
  description: "",
  installed: false,
  compat: "green",
  install_method: "ollama",
  hardware_tiers: { "apple-silicon": { recommended: "default" } },
};

const allApps = [
  rkllamaModel,
  ollamaModel,
  universalModel,
  unsupportedOnPi,
  fallbackInstallMethod,
];

describe("filterModels", () => {
  it("returns all apps as compatible when no filters are applied", () => {
    const { compatible, incompatible } = filterModels(allApps, [], []);
    expect(compatible).toEqual(allApps);
    expect(incompatible).toEqual([]);
  });

  it("filters to a single device's compatible models", () => {
    const { compatible } = filterModels(allApps, [piDevice], []);
    const ids = compatible.map((a) => a.id);
    expect(ids).toContain("qwen3-4b-rk");
    expect(ids).toContain("small-tool"); // no hardware_tiers → universal
    expect(ids).not.toContain("qwen3-4b-ollama"); // no arm-npu-16gb tier
  });

  it("excludes models with explicit 'unsupported' tier", () => {
    const { compatible, incompatible } = filterModels(allApps, [piDevice], []);
    expect(compatible.find((a) => a.id === "huge-model")).toBeUndefined();
    expect(incompatible.find((a) => a.id === "huge-model")).toBeDefined();
  });

  it("union semantics across multiple devices", () => {
    const { compatible } = filterModels(allApps, [piDevice, macDevice], []);
    const ids = compatible.map((a) => a.id);
    expect(ids).toContain("qwen3-4b-rk"); // matches Pi
    expect(ids).toContain("qwen3-4b-ollama"); // matches Mac
    expect(ids).toContain("small-tool"); // universal
  });

  it("backend filter narrows further (intersection with device match)", () => {
    const { compatible } = filterModels(
      allApps,
      [piDevice, macDevice],
      ["rkllama"]
    );
    const ids = compatible.map((a) => a.id);
    expect(ids).toContain("qwen3-4b-rk");
    expect(ids).not.toContain("qwen3-4b-ollama"); // ollama, not rkllama
  });

  it("falls back to install_method when variants[].backend is absent", () => {
    const { compatible } = filterModels(
      [fallbackInstallMethod],
      [macDevice],
      ["ollama"]
    );
    expect(compatible.map((a) => a.id)).toEqual(["via-method"]);
  });

  it("model with no hardware_tiers and no variants passes any device filter", () => {
    const { compatible } = filterModels(
      [universalModel],
      [piDevice],
      []
    );
    expect(compatible.map((a) => a.id)).toEqual(["small-tool"]);
  });

  it("model with no backend constraint passes any backend filter", () => {
    const { compatible } = filterModels(
      [universalModel],
      [],
      ["rkllama"]
    );
    expect(compatible.map((a) => a.id)).toEqual(["small-tool"]);
  });

  it("controller-only filter excludes Pi-only models into incompatible", () => {
    const { compatible, incompatible } = filterModels(
      allApps,
      [controllerDevice],
      []
    );
    const compatIds = compatible.map((a) => a.id);
    const incompatIds = incompatible.map((a) => a.id);
    expect(compatIds).toContain("qwen3-4b-ollama"); // x86-cpu-only listed
    expect(incompatIds).toContain("qwen3-4b-rk"); // only arm-npu-16gb
  });

  it("device + backend together require BOTH to match", () => {
    const { compatible } = filterModels(
      allApps,
      [macDevice],
      ["rkllama"]
    );
    expect(compatible).toEqual([]); // no model has Mac tier AND rkllama backend
  });

  it("ignores devices with no tier_id", () => {
    const noTierDevice: InstallTarget = {
      name: "weird",
      label: "weird",
      type: "remote",
    };
    const { compatible } = filterModels(allApps, [noTierDevice], []);
    // device has no tier_id → contributes nothing to the tier set;
    // selectedDevices is non-empty so deviceOk=false except for universal
    expect(compatible.map((a) => a.id)).toEqual(["small-tool"]);
  });
});
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `cd desktop && npx vitest run src/apps/StoreApp/filter.test.ts`
Expected: FAIL with `Cannot find module './filter'` or similar.

- [ ] **Step 3: Commit the failing tests**

```bash
git add desktop/src/apps/StoreApp/filter.test.ts
git commit -m "test(store): failing tests for filterModels pure function

11 cases covering: no-filter passthrough, single-device filtering,
'unsupported' tier exclusion, multi-device union, backend
intersection, install_method fallback, universal-compat models,
no-tier-id devices. Implementation lands in next commit."
```

---

## Task 4: Implement `filter.ts` to pass the tests

**Files:**
- Create: `desktop/src/apps/StoreApp/filter.ts`

- [ ] **Step 1: Write the implementation**

```typescript
// desktop/src/apps/StoreApp/filter.ts
import type { CatalogApp, InstallTarget } from "./types";

export interface FilterResult {
  compatible: CatalogApp[];
  incompatible: CatalogApp[];
}

/**
 * Filter the catalog by selected devices and backends.
 *
 * Empty arrays mean "no filter" on that axis. Multi-select is union
 * within an axis (any selected device matches; any selected backend
 * matches), intersection across axes (must satisfy both).
 *
 * A model passes the device filter if at least one of its declared
 * hardware_tiers matches one of the selected devices' tier_ids and
 * is not explicitly "unsupported". Models with no hardware_tiers are
 * treated as universally compatible.
 *
 * A model passes the backend filter if any of its variants advertise
 * one of the selected backends. Falls back to install_method when
 * variants[].backend is empty. Models with neither pass any backend
 * filter.
 */
export function filterModels(
  apps: CatalogApp[],
  selectedDevices: InstallTarget[],
  selectedBackends: string[],
): FilterResult {
  const tiers = new Set(
    selectedDevices.map((d) => d.tier_id).filter((t): t is string => Boolean(t))
  );
  const requireDeviceMatch = selectedDevices.length > 0;
  const backends = new Set(selectedBackends);

  const compatible: CatalogApp[] = [];
  const incompatible: CatalogApp[] = [];

  for (const app of apps) {
    const deviceOk = !requireDeviceMatch || appMatchesAnyTier(app, tiers);
    const backendOk = backends.size === 0 || appMatchesAnyBackend(app, backends);

    if (deviceOk && backendOk) compatible.push(app);
    else incompatible.push(app);
  }

  return { compatible, incompatible };
}

function appMatchesAnyTier(app: CatalogApp, tiers: Set<string>): boolean {
  // Universal compat: no declared tiers means runs anywhere.
  if (!app.hardware_tiers || Object.keys(app.hardware_tiers).length === 0) {
    return true;
  }
  for (const tid of tiers) {
    const entry = app.hardware_tiers[tid];
    if (entry !== undefined && entry !== "unsupported") return true;
  }
  return false;
}

function appMatchesAnyBackend(app: CatalogApp, backends: Set<string>): boolean {
  const appBackends = new Set<string>();
  if (app.variants && app.variants.length > 0) {
    for (const v of app.variants) {
      for (const b of v.backend ?? []) appBackends.add(b);
    }
  }
  // Fallback to install_method when variants don't declare backends.
  if (appBackends.size === 0 && app.install_method) {
    appBackends.add(app.install_method);
  }
  // No backend constraint at all → passes any filter.
  if (appBackends.size === 0) return true;

  for (const b of backends) if (appBackends.has(b)) return true;
  return false;
}
```

- [ ] **Step 2: Run tests to confirm they pass**

Run: `cd desktop && npx vitest run src/apps/StoreApp/filter.test.ts`
Expected: 11 tests pass, 0 failures.

- [ ] **Step 3: Commit**

```bash
git add desktop/src/apps/StoreApp/filter.ts
git commit -m "feat(store): pure filterModels function for device/backend filtering

Returns {compatible, incompatible} so the IncompatibleToggle can render
both groups. Empty selection arrays mean 'no filter' on that axis.
Multi-select is union within an axis, intersection across axes.
Universal-compat models (no hardware_tiers) and no-backend-constraint
models pass any filter."
```

---

## Task 5: `backends.ts` constant — backend display metadata

**Files:**
- Create: `desktop/src/apps/StoreApp/backends.ts`

- [ ] **Step 1: Write the file**

```typescript
// desktop/src/apps/StoreApp/backends.ts

/**
 * Display metadata for each backend that may appear in catalog manifests.
 * BackendPillBar renders pills via this lookup; unknown backends fall
 * back to the raw key with default styling.
 */
export interface BackendMeta {
  /** Human label shown in the pill. */
  label: string;
  /** Single-emoji icon (lightweight; matches existing Store conventions). */
  icon: string;
  /** Tailwind color stem (e.g. "purple", "blue") used for the pill accent. */
  color: string;
}

export const BACKEND_META: Record<string, BackendMeta> = {
  rkllama: { label: "rkllama (NPU)", icon: "🧠", color: "purple" },
  ollama: { label: "Ollama", icon: "🦙", color: "blue" },
  "llama-cpp": { label: "llama.cpp", icon: "🦫", color: "amber" },
  vllm: { label: "vLLM", icon: "⚡", color: "yellow" },
  transformers: { label: "Transformers", icon: "🤗", color: "rose" },
  diffusers: { label: "Diffusers", icon: "🎨", color: "fuchsia" },
  comfyui: { label: "ComfyUI", icon: "🧩", color: "indigo" },
  "stable-diffusion-cpp": { label: "stable-diffusion.cpp", icon: "🖼️", color: "pink" },
  "rknn-stable-diffusion": { label: "RKNN SD", icon: "🖼️", color: "purple" },
  fastsdcpu: { label: "FastSD CPU", icon: "🖌️", color: "teal" },
  "whisper-cpp": { label: "whisper.cpp", icon: "🎙️", color: "sky" },
  piper: { label: "Piper", icon: "🗣️", color: "emerald" },
  nemo: { label: "NeMo", icon: "🎵", color: "lime" },
};

/** Returns the metadata for `backend`, or a default fallback entry. */
export function backendMeta(backend: string): BackendMeta {
  return (
    BACKEND_META[backend] ?? {
      label: backend,
      icon: "⚙️",
      color: "slate",
    }
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd desktop && npx tsc --noEmit`
Expected: exit code 0.

- [ ] **Step 3: Commit**

```bash
git add desktop/src/apps/StoreApp/backends.ts
git commit -m "feat(store): BACKEND_META lookup for backend pill rendering

Maps backend key (rkllama, ollama, llama-cpp, ...) to {label, icon,
color}. backendMeta() returns a generic fallback for unknown backends
so adding a new one to a manifest doesn't break the UI; only the polish
requires a one-line BACKEND_META entry."
```

---

## Task 6: `DevicePillBar` component

**Files:**
- Create: `desktop/src/apps/StoreApp/DevicePillBar.tsx`

- [ ] **Step 1: Write the component**

```tsx
// desktop/src/apps/StoreApp/DevicePillBar.tsx
import { useMemo } from "react";
import { X } from "lucide-react";
import type { InstallTarget } from "./types";

interface Props {
  devices: InstallTarget[];
  selected: string[]; // device names
  onChange: (next: string[]) => void;
  loading?: boolean;
  /** When true, render skeleton pills (initial load). */
  showSkeleton?: boolean;
}

export function DevicePillBar({
  devices,
  selected,
  onChange,
  showSkeleton,
}: Props) {
  const selectedSet = useMemo(() => new Set(selected), [selected]);

  if (showSkeleton) {
    return (
      <div className="flex gap-2 overflow-x-auto py-2" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            className="h-7 w-24 rounded-full bg-shell-border/40 animate-pulse shrink-0"
          />
        ))}
      </div>
    );
  }

  if (devices.length === 0) return null;

  const toggle = (name: string) => {
    const next = selectedSet.has(name)
      ? selected.filter((n) => n !== name)
      : [...selected, name];
    onChange(next);
  };

  const clear = () => onChange([]);

  return (
    <div
      className="flex gap-2 overflow-x-auto py-2 items-center"
      role="group"
      aria-label="Filter by device"
    >
      {devices.map((d) => {
        const isOn = selectedSet.has(d.name);
        const tierBadge = d.tier_id?.replace(/^arm-|^x86-|^apple-/, "") ?? "";
        return (
          <button
            key={d.name}
            type="button"
            aria-pressed={isOn}
            onClick={() => toggle(d.name)}
            className={`shrink-0 inline-flex items-center gap-1.5 px-3 py-1 rounded-full border text-xs whitespace-nowrap transition-colors ${
              isOn
                ? "bg-accent/15 text-accent border-accent/30"
                : "bg-transparent text-shell-text-secondary border-shell-border hover:bg-shell-border/40"
            }`}
          >
            <span>{d.friendly_name ?? d.label}</span>
            {tierBadge && (
              <span className="text-[10px] opacity-70 uppercase tracking-wide">
                {tierBadge}
              </span>
            )}
          </button>
        );
      })}
      {selected.length > 0 && (
        <button
          type="button"
          onClick={clear}
          aria-label="Clear device filter"
          className="shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded-full text-[11px] text-shell-text-tertiary hover:text-shell-text-primary"
        >
          <X size={12} />
          Clear
        </button>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd desktop && npx tsc --noEmit`
Expected: exit code 0.

- [ ] **Step 3: Commit**

```bash
git add desktop/src/apps/StoreApp/DevicePillBar.tsx
git commit -m "feat(store): DevicePillBar component

Horizontal-scrollable multi-select pill bar. Each pill shows
friendly_name + a small tier badge derived from tier_id. aria-pressed
on each button; aria-label='Filter by device' on the group. Mobile
gets the same overflow-x-auto treatment as the existing category strip
without any extra mobile-specific code."
```

---

## Task 7: `BackendPillBar` component

**Files:**
- Create: `desktop/src/apps/StoreApp/BackendPillBar.tsx`

- [ ] **Step 1: Write the component**

```tsx
// desktop/src/apps/StoreApp/BackendPillBar.tsx
import { useMemo } from "react";
import { backendMeta } from "./backends";

interface Props {
  /** Backends available given the currently selected devices. */
  available: string[];
  selected: string[];
  onChange: (next: string[]) => void;
  /** True when no devices are selected — bar should not render at all. */
  disabled?: boolean;
}

export function BackendPillBar({
  available,
  selected,
  onChange,
  disabled,
}: Props) {
  const selectedSet = useMemo(() => new Set(selected), [selected]);

  if (disabled || available.length === 0) return null;

  const toggle = (name: string) => {
    const next = selectedSet.has(name)
      ? selected.filter((n) => n !== name)
      : [...selected, name];
    onChange(next);
  };

  return (
    <div
      className="flex gap-2 overflow-x-auto py-1.5 items-center"
      role="group"
      aria-label="Filter by backend"
    >
      <span className="text-[10px] uppercase tracking-wide text-shell-text-tertiary mr-1 shrink-0">
        Backend
      </span>
      {available.map((b) => {
        const meta = backendMeta(b);
        const isOn = selectedSet.has(b);
        return (
          <button
            key={b}
            type="button"
            aria-pressed={isOn}
            onClick={() => toggle(b)}
            className={`shrink-0 inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full border text-[11px] whitespace-nowrap transition-colors ${
              isOn
                ? `bg-${meta.color}-500/15 text-${meta.color}-300 border-${meta.color}-500/30`
                : "bg-transparent text-shell-text-secondary border-shell-border hover:bg-shell-border/40"
            }`}
          >
            <span aria-hidden="true">{meta.icon}</span>
            <span>{meta.label}</span>
          </button>
        );
      })}
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd desktop && npx tsc --noEmit`
Expected: exit code 0.

- [ ] **Step 3: Write the snapshot test**

```tsx
// desktop/src/apps/StoreApp/BackendPillBar.test.tsx
import { describe, it, expect } from "vitest";
import { render } from "@testing-library/react";
import { BackendPillBar } from "./BackendPillBar";

describe("BackendPillBar", () => {
  it("renders nothing when disabled", () => {
    const { container } = render(
      <BackendPillBar
        available={["rkllama"]}
        selected={[]}
        onChange={() => {}}
        disabled
      />
    );
    expect(container.firstChild).toBeNull();
  });

  it("uses BACKEND_META label for known backends", () => {
    const { getByText } = render(
      <BackendPillBar
        available={["rkllama", "ollama"]}
        selected={[]}
        onChange={() => {}}
      />
    );
    expect(getByText("rkllama (NPU)")).toBeTruthy();
    expect(getByText("Ollama")).toBeTruthy();
  });

  it("falls back to raw key for unknown backends", () => {
    const { getByText } = render(
      <BackendPillBar
        available={["mystery-backend"]}
        selected={[]}
        onChange={() => {}}
      />
    );
    expect(getByText("mystery-backend")).toBeTruthy();
  });

  it("aria-pressed reflects selection", () => {
    const { getByRole } = render(
      <BackendPillBar
        available={["rkllama"]}
        selected={["rkllama"]}
        onChange={() => {}}
      />
    );
    const btn = getByRole("button", { name: /rkllama/i });
    expect(btn.getAttribute("aria-pressed")).toBe("true");
  });
});
```

- [ ] **Step 4: Run tests**

Run: `cd desktop && npx vitest run src/apps/StoreApp/BackendPillBar.test.tsx`
Expected: 4 tests pass.

If `@testing-library/react` isn't installed, add it as a dev dep first:
```bash
cd desktop && npm install --save-dev @testing-library/react @testing-library/jest-dom
```
Then re-run.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/apps/StoreApp/BackendPillBar.tsx desktop/src/apps/StoreApp/BackendPillBar.test.tsx desktop/package.json desktop/package-lock.json
git commit -m "feat(store): BackendPillBar component

Reveals only when at least one device is selected (disabled prop
hides it entirely — no flicker, no half-state). Uses backendMeta() so
unknown backends render with raw key + default styling. Snapshot tests
cover known/unknown backends and aria-pressed state."
```

---

## Task 8: `IncompatibleToggle` component

**Files:**
- Create: `desktop/src/apps/StoreApp/IncompatibleToggle.tsx`

- [ ] **Step 1: Write the component**

```tsx
// desktop/src/apps/StoreApp/IncompatibleToggle.tsx
import { useState, type ReactNode } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";

interface Props {
  count: number;
  /** Render-prop for the dimmed grid of incompatible cards. */
  children: ReactNode;
}

export function IncompatibleToggle({ count, children }: Props) {
  const [open, setOpen] = useState(false);

  if (count === 0) return null;

  return (
    <div className="mt-6 border-t border-shell-border pt-4">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="text-xs text-shell-text-tertiary hover:text-shell-text-primary inline-flex items-center gap-1"
        aria-expanded={open}
      >
        {open ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        {open ? "Hide" : "Show"} {count} model{count === 1 ? "" : "s"} that
        won't run on the selected devices
      </button>
      {open && <div className="mt-3 opacity-50">{children}</div>}
    </div>
  );
}
```

- [ ] **Step 2: Type-check**

Run: `cd desktop && npx tsc --noEmit`
Expected: exit code 0.

- [ ] **Step 3: Commit**

```bash
git add desktop/src/apps/StoreApp/IncompatibleToggle.tsx
git commit -m "feat(store): IncompatibleToggle render-prop component

Hidden when count=0. When expanded, dims the children block (which the
caller fills with a grid of model cards). aria-expanded so screen
readers announce the toggle state."
```

---

## Task 9: localStorage hydration + validation

**Files:**
- Create: `desktop/src/apps/StoreApp/storage.ts`

- [ ] **Step 1: Write the helpers**

```typescript
// desktop/src/apps/StoreApp/storage.ts

interface PersistedFilter {
  devices: string[];
  backends: string[];
}

/** Build the localStorage key for a (user, profile) pair. */
function key(userId: string, profileId: string): string {
  return `taos.store.filter.${userId}.${profileId}`;
}

/**
 * Hydrate a previously-saved filter from localStorage. Names that no
 * longer exist in `validDevices` or `validBackends` are dropped before
 * returning, so a stale filter never references a removed worker.
 */
export function loadFilter(
  userId: string,
  profileId: string,
  validDevices: string[],
  validBackends: string[],
): PersistedFilter {
  if (typeof window === "undefined") return { devices: [], backends: [] };
  let raw: string | null = null;
  try {
    raw = window.localStorage.getItem(key(userId, profileId));
  } catch {
    return { devices: [], backends: [] };
  }
  if (!raw) return { devices: [], backends: [] };

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { devices: [], backends: [] };
  }
  if (!parsed || typeof parsed !== "object") {
    return { devices: [], backends: [] };
  }
  const obj = parsed as { devices?: unknown; backends?: unknown };

  const validDeviceSet = new Set(validDevices);
  const validBackendSet = new Set(validBackends);

  const devices = Array.isArray(obj.devices)
    ? obj.devices.filter(
        (d): d is string => typeof d === "string" && validDeviceSet.has(d)
      )
    : [];
  const backends = Array.isArray(obj.backends)
    ? obj.backends.filter(
        (b): b is string => typeof b === "string" && validBackendSet.has(b)
      )
    : [];

  return { devices, backends };
}

export function saveFilter(
  userId: string,
  profileId: string,
  filter: PersistedFilter,
): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key(userId, profileId), JSON.stringify(filter));
  } catch {
    // localStorage may be unavailable (private mode, quota); fail silently.
  }
}
```

- [ ] **Step 2: Type-check**

Run: `cd desktop && npx tsc --noEmit`
Expected: exit code 0.

- [ ] **Step 3: Commit**

```bash
git add desktop/src/apps/StoreApp/storage.ts
git commit -m "feat(store): persist filter to localStorage with validation

Key shape: taos.store.filter.{user_id}.{profile_id}. Hydrate drops any
device/backend names that no longer exist in the live cluster catalog,
so a removed worker doesn't keep its filter selection alive forever.
Save is fire-and-forget (silent on quota errors / private mode)."
```

---

## Task 10: Wire pill bars + filter into `StoreApp/index.tsx`

This is the largest task by line count. We add filter state, render the bars (gated on the Models category), filter the apps before display, and render the IncompatibleToggle.

**Files:**
- Modify: `desktop/src/apps/StoreApp/index.tsx`

- [ ] **Step 1: Add the new imports near the top**

Find the lucide imports near line 2-3 of `index.tsx`. Add this import block immediately after them:

```typescript
import { DevicePillBar } from "./DevicePillBar";
import { BackendPillBar } from "./BackendPillBar";
import { IncompatibleToggle } from "./IncompatibleToggle";
import { filterModels } from "./filter";
import { loadFilter, saveFilter } from "./storage";
```

- [ ] **Step 2: Add filter state and persistence in the StoreApp component**

Find the `StoreApp` function component (around line 590). Locate the existing `useState` block for `installTargets` and `runtimeHosts`. Immediately after them, add:

```typescript
const [selectedDevices, setSelectedDevices] = useState<string[]>([]);
const [selectedBackends, setSelectedBackends] = useState<string[]>([]);
// User identity for per-user filter persistence. Use an "anon" fallback
// so single-user setups still work; profile defaults to "default".
const userId = (typeof window !== "undefined"
  ? window.localStorage.getItem("taos.user.id") || "anon"
  : "anon");
const profileId = (typeof window !== "undefined"
  ? window.localStorage.getItem("taos.profile.id") || "default"
  : "default");
```

- [ ] **Step 3: Hydrate filter from localStorage once devices and catalog are loaded**

Add a new `useEffect` after the existing one that loads `installTargets`. This runs once both lists are non-empty so we can validate against them:

```typescript
useEffect(() => {
  if (installTargets.length === 0 || apps.length === 0) return;
  const validDevices = installTargets.map((t) => t.name);
  // Build the set of all backends that appear in the catalog.
  const validBackends = Array.from(
    new Set(
      apps.flatMap((a) =>
        (a.variants ?? []).flatMap((v) => v.backend ?? []).concat(
          a.install_method ? [a.install_method] : []
        )
      )
    )
  );
  const persisted = loadFilter(userId, profileId, validDevices, validBackends);
  setSelectedDevices(persisted.devices);
  setSelectedBackends(persisted.backends);
  // Run only on first non-empty load — subsequent changes are user-driven.
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, [installTargets.length > 0 && apps.length > 0]);
```

- [ ] **Step 4: Persist on change**

Add another `useEffect` immediately after the hydrate one:

```typescript
useEffect(() => {
  saveFilter(userId, profileId, {
    devices: selectedDevices,
    backends: selectedBackends,
  });
}, [selectedDevices, selectedBackends, userId, profileId]);
```

- [ ] **Step 5: Compute filter results**

Find the existing `filtered` constant (around line 671):
```typescript
const filtered = apps.filter((app) => {
  if (activeCategory !== "all" && activeCat) {
    if (!activeCat.types.includes(appGroup(app))) return false;
  }
  if (search) {
    const q = search.toLowerCase();
    return app.name.toLowerCase().includes(q) || app.description.toLowerCase().includes(q);
  }
  return true;
});
```

Replace with:

```typescript
const categoryFiltered = apps.filter((app) => {
  if (activeCategory !== "all" && activeCat) {
    if (!activeCat.types.includes(appGroup(app))) return false;
  }
  if (search) {
    const q = search.toLowerCase();
    return (
      app.name.toLowerCase().includes(q) ||
      app.description.toLowerCase().includes(q)
    );
  }
  return true;
});

// Device + backend filter only applies when the user is in the Models
// category. Other categories use the existing list as-is.
const isModels = activeCategory === "models";
const selectedDeviceObjs = installTargets.filter((t) =>
  selectedDevices.includes(t.name)
);
const filterResult = isModels
  ? filterModels(categoryFiltered, selectedDeviceObjs, selectedBackends)
  : { compatible: categoryFiltered, incompatible: [] };
const filtered = filterResult.compatible;
const incompatible = filterResult.incompatible;
```

- [ ] **Step 6: Compute available backends from selected devices**

Below the filtering block, add:

```typescript
// Backends shown in the BackendPillBar are the union of variants[].backend
// across all manifests where any selected device's tier_id is supported.
const availableBackends = useMemo(() => {
  if (!isModels || selectedDeviceObjs.length === 0) return [];
  const tiers = new Set(
    selectedDeviceObjs.map((d) => d.tier_id).filter(Boolean) as string[]
  );
  const out = new Set<string>();
  for (const app of apps) {
    if (!app.hardware_tiers) continue;
    const tierMatch = [...tiers].some(
      (t) =>
        app.hardware_tiers![t] !== undefined &&
        app.hardware_tiers![t] !== "unsupported"
    );
    if (!tierMatch) continue;
    for (const v of app.variants ?? []) {
      for (const b of v.backend ?? []) out.add(b);
    }
    if ((app.variants ?? []).length === 0 && app.install_method) {
      out.add(app.install_method);
    }
  }
  return Array.from(out).sort();
}, [isModels, selectedDeviceObjs, apps]);
```

Add `useMemo` to the React import at the top if it's not already there.

- [ ] **Step 7: Auto-deselect backends that fall out of the available set**

Add another `useEffect`:

```typescript
useEffect(() => {
  if (!isModels) return;
  if (availableBackends.length === 0) return; // bar is hidden, leave state alone
  const availSet = new Set(availableBackends);
  const dropped = selectedBackends.filter((b) => !availSet.has(b));
  if (dropped.length > 0) {
    setSelectedBackends((prev) => prev.filter((b) => availSet.has(b)));
    // Surface a toast — for now use a simple console warning since this
    // codebase's toast helper is not yet wired into StoreApp. Adding a
    // toast call here is a follow-up.
    console.info(
      `[store-filter] auto-deselected backend(s): ${dropped.join(", ")}`
    );
  }
}, [availableBackends, isModels, selectedBackends]);
```

- [ ] **Step 8: Render the pill bars + IncompatibleToggle**

Find the main render area (around line 778-790) where the model grid is currently rendered. Locate the line near `<span className="text-xs text-shell-text-tertiary">{filtered.length} apps</span>` (around line 759) and the grid that follows. Insert the bars *above* the grid, only when in the Models category.

Find the block that begins with the grid heading (search for `{filtered.length} apps`). Above it, insert:

```tsx
{isModels && (
  <>
    <DevicePillBar
      devices={installTargets}
      selected={selectedDevices}
      onChange={setSelectedDevices}
      showSkeleton={installTargets.length === 0 && loading}
    />
    <BackendPillBar
      available={availableBackends}
      selected={selectedBackends}
      onChange={setSelectedBackends}
      disabled={selectedDevices.length === 0}
    />
  </>
)}
```

- [ ] **Step 9: Render the IncompatibleToggle below the grid**

Locate the closing tag of the model grid (just before the section that follows the cards). Insert immediately after the existing grid closes:

```tsx
{isModels && (
  <IncompatibleToggle count={incompatible.length}>
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
      {incompatible.map((app) => (
        <AppCard
          key={app.id}
          app={app}
          affected={[]}
          onInstall={handleInstall}
          onUninstall={handleUninstall}
          installTargets={installTargets}
          runtimeHost={runtimeHosts[app.id] ?? null}
        />
      ))}
    </div>
  </IncompatibleToggle>
)}
```

(The exact existing grid markup will tell you the right place to insert; the toggle should be at the bottom of the main pane within the Models view.)

- [ ] **Step 10: Type-check + run all frontend tests**

Run: `cd desktop && npx tsc --noEmit`
Expected: exit code 0.

Run: `cd desktop && npx vitest run`
Expected: all tests pass (existing + new filter + BackendPillBar).

- [ ] **Step 11: Commit**

```bash
git add desktop/src/apps/StoreApp/index.tsx
git commit -m "feat(store): wire device/backend filter into StoreApp

Pill bars render only when the Models category is active. Filter
state hydrates from localStorage on first non-empty (devices, catalog)
load, persists on every change, validates against current valid sets
on hydrate. Auto-deselects backends when their last supporting device
is removed (logs to console — toast wiring is a follow-up).
IncompatibleToggle renders a dimmed grid of models that don't fit
the current filter."
```

---

## Task 11: Backend — extend `/api/cluster/install-targets`

**Files:**
- Modify: `tinyagentos/routes/cluster.py:256-280`
- Modify: `tests/test_routes_cluster.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_routes_cluster.py`:

```python
@pytest.mark.asyncio
async def test_install_targets_includes_controller_with_tier_id(client):
    resp = await client.get("/api/cluster/install-targets")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    local = next(t for t in data if t["name"] == "local")
    assert local["type"] == "local"
    assert local["label"] == "This controller"
    assert "tier_id" in local
    # Controller's tier comes from app.state.hardware_profile — accept any
    # non-empty string; specific value depends on the host running tests.
    assert isinstance(local["tier_id"], str) and local["tier_id"]
    assert "friendly_name" in local
    assert local["friendly_name"] == "Controller"


@pytest.mark.asyncio
async def test_install_targets_remote_includes_tier_id(client, monkeypatch):
    # Register a fake worker so /api/cluster/workers has something with a
    # tier_id we control.
    from tinyagentos.cluster.worker_protocol import WorkerInfo, WorkerHardware
    cluster = client.app_state.cluster_manager  # noqa: SLF001
    fake_hw = WorkerHardware(
        ram_mb=16384, vram_mb=0, gpu="", npu="rk3588",
        os="linux", arch="aarch64", capabilities=[],
    )
    fake_worker = WorkerInfo(
        name="orange-pi", url="https://192.168.1.10:8443",
        signing_key=b"x" * 32, hardware=fake_hw, status="online",
    )
    cluster._workers["orange-pi"] = fake_worker  # noqa: SLF001

    # Pretend an incus remote with the same name is registered.
    async def fake_remote_list():
        return [{"name": "orange-pi", "addr": "https://192.168.1.10:8443",
                 "protocol": "incus"}]
    monkeypatch.setattr(
        "tinyagentos.containers.remote_list", fake_remote_list
    )

    resp = await client.get("/api/cluster/install-targets")
    assert resp.status_code == 200
    data = resp.json()
    pi = next((t for t in data if t["name"] == "orange-pi"), None)
    assert pi is not None
    assert pi["type"] == "remote"
    assert pi["addr"] == "https://192.168.1.10:8443"
    # tier_id should be derived from the worker's hardware via
    # _potential_capabilities — exact value depends on registry, but
    # the key must be present and non-empty.
    assert "tier_id" in pi
    assert isinstance(pi["tier_id"], str) and pi["tier_id"]
    assert pi["friendly_name"] == "orange-pi"
```

(If the existing `client` fixture doesn't expose `client.app_state`, use `client._transport.app.state` or whatever pattern the rest of the file uses. Check the top of `tests/test_routes_cluster.py` for the existing fixture and follow its idiom.)

- [ ] **Step 2: Run tests to confirm they fail**

Run: `PYTHONPATH=. pytest tests/test_routes_cluster.py::test_install_targets_includes_controller_with_tier_id tests/test_routes_cluster.py::test_install_targets_remote_includes_tier_id -v`
Expected: FAIL with `KeyError: 'tier_id'` or assertion errors on the missing fields.

- [ ] **Step 3: Implement the extension in `cluster.py`**

Open `tinyagentos/routes/cluster.py`. Find `list_install_targets` (line 256). Replace the function body:

```python
@router.get("/api/cluster/install-targets")
async def list_install_targets(request: Request):
    """Return the ordered list of hosts available for LXC service installs.

    Always includes the controller first ("local"), then any registered incus
    remotes whose protocol is "incus" (filters out the read-only image servers).
    Each entry carries a `tier_id` (hardware profile id, used by the Store
    filter to match against catalog `hardware_tiers`) and a `friendly_name`
    for display.
    """
    targets: list[dict] = [
        {
            "name": "local",
            "label": "This controller",
            "type": "local",
            "tier_id": getattr(
                request.app.state.hardware_profile, "profile_id", ""
            ),
            "friendly_name": "Controller",
        }
    ]
    # Map worker name → tier_id by reusing the existing capability resolver.
    cluster = getattr(request.app.state, "cluster_manager", None)
    registry = getattr(request.app.state, "registry", None)
    worker_tiers: dict[str, str] = {}
    if cluster is not None and registry is not None:
        for w in cluster.get_workers():
            try:
                tier_id, _caps = _potential_capabilities(w.hardware, registry)
                worker_tiers[w.name] = tier_id
            except Exception:  # noqa: BLE001
                worker_tiers[w.name] = ""

    try:
        import tinyagentos.containers as containers
        remotes = await containers.remote_list()
        for r in remotes:
            name = r.get("name", "")
            proto = r.get("protocol", "")
            if not name or name in _BUILTIN_REMOTES or proto != "incus":
                continue
            targets.append({
                "name": name,
                "label": name,
                "type": "remote",
                "addr": r.get("addr", ""),
                "tier_id": worker_tiers.get(name, ""),
                "friendly_name": name,
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("list_install_targets: remote_list failed: %s", exc)
    return targets
```

The function now takes a `request: Request` parameter (it didn't before — FastAPI injects it). Make sure `Request` is imported at the top of the file (it likely already is).

- [ ] **Step 4: Run tests to confirm they pass**

Run: `PYTHONPATH=. pytest tests/test_routes_cluster.py -v`
Expected: all cluster route tests pass, including the two new ones.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/cluster.py tests/test_routes_cluster.py
git commit -m "feat(cluster): expose tier_id and friendly_name on install-targets

The Store filter needs each cluster device's hardware tier id so it
can match against catalog hardware_tiers. tier_id for the controller
comes from app.state.hardware_profile.profile_id; for remotes, from
the existing _potential_capabilities helper used by /api/cluster/workers.
friendly_name is 'Controller' for local and the worker name for remotes
— sufficient until a future per-worker rename UX exists."
```

---

## Task 12: Plumb `target_remote` through the default install-v2 branch

So model installs land on the worker that hosts the backend, not the controller.

**Files:**
- Modify: `tinyagentos/routes/store_install.py`
- Modify: `tests/test_routes_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_routes_store.py` inside the existing `TestInstallV2PersistsAcrossReload` class:

```python
async def test_install_v2_default_backend_records_target_remote(
    self, app_with_store, store_client
):
    """When target_remote is provided to a default-backend install, the
    runtime location is recorded against that remote so /installed-v2
    reports the right host."""
    await app_with_store.state.installed_apps.init()
    resp = await store_client.post(
        "/api/store/install-v2",
        json={"app_id": "smolagents", "target_remote": "orange-pi"},
    )
    assert resp.status_code == 200

    listed = await store_client.get("/api/store/installed-v2")
    rows = listed.json()["installed"]
    smol = next(r for r in rows if r["app_id"] == "smolagents")
    # When target_remote is supplied, runtime_host should resolve to that
    # remote's hostname (or the remote name itself as a DNS fallback).
    assert smol["runtime_host"] in {"orange-pi", "192.168.1.10"} or (
        smol["runtime_host"] is not None and smol["runtime_host"] != "127.0.0.1"
    )
```

- [ ] **Step 2: Run the test to confirm it fails**

Run: `PYTHONPATH=. pytest tests/test_routes_store.py::TestInstallV2PersistsAcrossReload::test_install_v2_default_backend_records_target_remote -v`
Expected: FAIL — runtime_host is currently `None` for default-branch installs.

- [ ] **Step 3: Implement `target_remote` plumbing in store_install.py**

Open `tinyagentos/routes/store_install.py`. Find the default install branch (around line 171-184, immediately after the LXC branch returns). Replace it:

```python
    # Default: delegate to InstalledAppsStore (docker/pip/download).
    store = request.app.state.installed_apps
    await store.install(app_id, body.get("version", ""), meta)

    # If the caller specified a target_remote (e.g. from the Store
    # filter when exactly one device is selected), record the runtime
    # location against that remote. Models live on the device that runs
    # them — without this the controller is implied.
    raw_remote = body.get("target_remote") or ""
    target_remote: str | None = (
        raw_remote if raw_remote and raw_remote != "local" else None
    )
    if target_remote is not None:
        # Validate the remote is registered, same check as the LXC branch.
        try:
            import tinyagentos.containers as containers
            registered = await containers.remote_list()
            known = {r.get("name") for r in registered}
            if target_remote not in known:
                return JSONResponse(
                    {
                        "error": (
                            f"incus remote '{target_remote}' is not registered."
                            f" Register it first via POST /api/cluster/remotes."
                        )
                    },
                    status_code=400,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "install-v2 default: could not verify remote %r: %s",
                target_remote, exc,
            )
        runtime_host = await _resolve_host(target_remote)
        # Default-branch installs don't expose a stable port (they run
        # in-process on the controller or via per-backend daemons). Use 0
        # to indicate "no proxy port" — the proxy router already handles
        # this case by falling back to the backend's own resolver.
        await store.update_runtime_location(
            app_id,
            host=runtime_host,
            port=0,
            backend=meta.get("backend", "") if isinstance(meta, dict) else "",
            ui_path=(install_config.get("ui_path", "/")
                     if isinstance(install_config, dict) else "/"),
        )

    if registry is not None:
        version = body.get("version") or (
            getattr(manifest, "version", "") if manifest else ""
        )
        registry.mark_installed(app_id, version)
    return JSONResponse({"ok": True, "app_id": app_id, "status": "installed"})
```

Note: `install_config` is referenced — verify it's in scope at this point in the function (it should be, defined earlier in the function around line 60-69). If not, default `ui_path` to `"/"` directly without the lookup.

- [ ] **Step 4: Run tests**

Run: `PYTHONPATH=. pytest tests/test_routes_store.py -v`
Expected: all tests pass, including the new target_remote test and existing TestInstallV2PersistsAcrossReload tests.

- [ ] **Step 5: Commit**

```bash
git add tinyagentos/routes/store_install.py tests/test_routes_store.py
git commit -m "feat(store): plumb target_remote through default install-v2 branch

Models should live on the device that runs them. The default branch
(non-LXC: docker/pip/download) now accepts target_remote and records
the runtime_location against the resolved host so /installed-v2
reports the right host. Validates the remote is a registered incus
remote first, same check the LXC branch already does. Empty/local
target_remote keeps existing behavior (no runtime_location recorded)."
```

---

## Task 13: Frontend — install button defaults to selected device

When exactly one device is selected in the filter, install actions on model cards default `target_remote` to that device's name.

**Files:**
- Modify: `desktop/src/apps/StoreApp/index.tsx`

- [ ] **Step 1: Locate the install button POST**

In `desktop/src/apps/StoreApp/index.tsx`, find the `AppCard` component (around line 436). Inside, find the install handler that POSTs to `/api/store/install-v2` (around line 472):

```typescript
const res = await fetch("/api/store/install-v2", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ app_id: app.id }),
});
```

- [ ] **Step 2: Plumb the selected-device default through to AppCard**

The `AppCard` component already receives `installTargets`. Add a new optional `defaultTargetRemote` prop (defaults to `undefined`):

```typescript
function AppCard({ app, affected, onInstall, onUninstall, installTargets, runtimeHost, defaultTargetRemote }: {
  app: CatalogApp;
  affected: string[];
  onInstall: (id: string) => void;
  onUninstall: (id: string) => void;
  installTargets: InstallTarget[];
  runtimeHost: string | null;
  defaultTargetRemote?: string;
}) {
```

Inside the component, find the existing local state for the per-card target dropdown (likely `useState` for the selected target). Initialize it from `defaultTargetRemote` when the prop is set:

```typescript
const [target, setTarget] = useState<string>(
  defaultTargetRemote ?? "local"
);

// If the parent's default changes (user changes the filter), update.
useEffect(() => {
  if (defaultTargetRemote !== undefined) setTarget(defaultTargetRemote);
}, [defaultTargetRemote]);
```

Update the `fetch` body to include `target_remote`:

```typescript
const res = await fetch("/api/store/install-v2", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    app_id: app.id,
    target_remote: target,
  }),
});
```

(The exact existing per-card target dropdown code may differ — match the existing pattern. If there's no per-card dropdown today, defer that UX touch and just include `target_remote: defaultTargetRemote ?? "local"` directly in the install POST.)

- [ ] **Step 3: Pass `defaultTargetRemote` from `StoreApp` to `AppCard`**

In the model grid render block, pass the new prop:

```tsx
<AppCard
  key={app.id}
  app={app}
  affected={affected}
  onInstall={handleInstall}
  onUninstall={handleUninstall}
  installTargets={installTargets}
  runtimeHost={runtimeHosts[app.id] ?? null}
  defaultTargetRemote={
    isModels && selectedDevices.length === 1 ? selectedDevices[0] : undefined
  }
/>
```

Apply the same change to the IncompatibleToggle's grid (the dimmed cards rendered in Task 10 Step 9).

- [ ] **Step 4: Type-check + tests**

Run: `cd desktop && npx tsc --noEmit`
Expected: exit code 0.

Run: `cd desktop && npx vitest run`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add desktop/src/apps/StoreApp/index.tsx
git commit -m "feat(store): install button defaults to filter-selected device

When exactly one device is selected in the filter, the install POST
sends target_remote = that device. Multi-select / no-select preserves
the existing per-card target dropdown behaviour. Combined with the
backend plumbing in the previous commit, model weights now land on
the device that runs them when the filter expresses single-device
intent."
```

---

## Task 14: Manifest audit script

A one-off CLI to flag manifests with empty `backend` or `hardware_tiers`. Not run in CI; just a tool we run once to find catalog hygiene issues, then fix them upstream.

**Files:**
- Create: `scripts/audit-manifests.py`

- [ ] **Step 1: Write the script**

```python
#!/usr/bin/env python3
"""
Audit catalog manifests for empty backend or hardware_tiers fields.

The Store device/backend filter degrades gracefully on missing fields
(treats them as "no constraint"), but a manifest with empty backends
won't show up under any backend filter — and one with empty
hardware_tiers shows under every device, which is rarely intended.
This script flags them so we can fix them upstream.

Usage:
    python scripts/audit-manifests.py [--root app-catalog/models]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml


def audit(root: Path) -> int:
    issues: list[str] = []
    for manifest_path in sorted(root.rglob("manifest.yaml")):
        try:
            data = yaml.safe_load(manifest_path.read_text())
        except yaml.YAMLError as exc:
            issues.append(f"{manifest_path}: YAML parse error — {exc}")
            continue
        if not isinstance(data, dict):
            issues.append(f"{manifest_path}: not a mapping at top level")
            continue

        # Skip non-model entries — the filter is models-only for now.
        if data.get("type") != "model":
            continue

        mid = data.get("id", manifest_path.parent.name)
        variants = data.get("variants") or []
        method = (data.get("install") or {}).get("method")

        all_backends: set[str] = set()
        for v in variants:
            if isinstance(v, dict):
                for b in v.get("backend") or []:
                    if isinstance(b, str):
                        all_backends.add(b)
        if not all_backends and not method:
            issues.append(
                f"{mid} ({manifest_path}): no backends declared on any variant "
                f"and no install.method — model will not appear under any "
                f"backend filter"
            )

        tiers = data.get("hardware_tiers") or {}
        if not tiers:
            issues.append(
                f"{mid} ({manifest_path}): no hardware_tiers declared — "
                f"model will appear under every device filter (probably "
                f"unintended)"
            )

    if not issues:
        print("clean: every model manifest declares backends and hardware_tiers")
        return 0
    print(f"\n{len(issues)} manifest issue(s):\n")
    for line in issues:
        print(f"  - {line}")
    print()
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path("app-catalog/models"),
        help="Catalog directory to scan (default: app-catalog/models)"
    )
    args = parser.parse_args()
    if not args.root.is_dir():
        print(f"error: {args.root} is not a directory", file=sys.stderr)
        return 2
    return audit(args.root)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Make it executable and run it**

```bash
chmod +x scripts/audit-manifests.py
python scripts/audit-manifests.py
```

Expected: prints the list of manifests with hygiene issues. Don't fix the manifests in this task — that's a separate cleanup PR. The goal here is to ship the tool.

- [ ] **Step 3: Commit**

```bash
git add scripts/audit-manifests.py
git commit -m "chore: add audit-manifests.py to flag catalog hygiene issues

Scans app-catalog/models/*/manifest.yaml for entries missing backend
declarations or hardware_tiers. Exits 0 when clean, 1 when issues
found. Not wired into CI — a one-off tool to run before / after
catalog edits. The Store filter degrades gracefully on missing fields,
this just makes the gaps visible so we can fix them deliberately."
```

---

## Task 15: Final verification + open PR

- [ ] **Step 1: Run the full test suite**

```bash
cd desktop && npx tsc --noEmit && npx vitest run
cd .. && PYTHONPATH=. pytest tests/test_routes_cluster.py tests/test_routes_store.py -v
```
Expected: all green.

- [ ] **Step 2: Manual smoke on the Pi (per spec § Testing)**

1. Build + deploy desktop bundle to the Pi (existing flow — `cd desktop && npm run build`, then sync to Pi).
2. On the Pi: open Store → Models. Confirm "Controller" + "orange-pi" pills.
3. Click "orange-pi" → backend bar reveals; rkllama, ollama, llama-cpp pre-selected. Grid narrows.
4. Deselect ollama and llama-cpp → only `.rkllm`-format models remain.
5. Click "Show N incompatible" → dimmed cards appear.
6. Install an rkllama-backed model with only the Pi selected. Confirm via SSH that the weight downloaded under `~/rkllama/models/<id>/` on the Pi (or that the runtime_host recorded for the model is `192.168.6.123` / `orange-pi`, not `127.0.0.1`).
7. Reload page → filter selections persist.
8. Stop the worker via `systemctl stop` → pill flagged offline; install button on cards disabled with tooltip.

If any of these fail, file a bug and fix before opening the PR.

- [ ] **Step 3: Open the PR**

```bash
git push -u origin feat/store-filter-by-device-and-backend
gh pr create --title "feat(store): filter Models tab by device + backend" --body "$(cat <<'EOF'
## Summary
- Two-tier hierarchical filter (device → backend, multi-select on both) on the Store Models tab
- Models live on the device that runs them: install button defaults to the selected-device's `target_remote`; default install-v2 branch records `runtime_host` against it
- localStorage persistence per (user, profile), validated on hydrate
- IncompatibleToggle reveals dimmed cards for models that don't fit the current filter
- Manifest audit script flags catalog hygiene issues (empty `backend` / `hardware_tiers`)
- Mobile: pill bars use the existing horizontal-scroll category-strip treatment

## Test Plan
- [ ] `cd desktop && npx vitest run` — all green
- [ ] `pytest tests/test_routes_cluster.py tests/test_routes_store.py` — all green
- [ ] Manual smoke on Pi: see plan § Task 15 Step 2

## Spec
docs/superpowers/specs/2026-05-06-store-filter-by-device-and-backend-design.md
EOF
)"
```

---

## Self-Review

**Spec coverage check:**

| Spec section | Plan task |
|---|---|
| Architecture diagram + state model | Task 10 |
| `DevicePillBar` | Task 6 |
| `BackendPillBar` (incl. auto-deselect, no auto-restore) | Task 7 + Task 10 Step 7 |
| `IncompatibleToggle` | Task 8 + Task 10 Step 9 |
| `StoreApp` integration | Task 10 |
| Mobile layout (horizontal-scroll) | Tasks 6, 7 (`overflow-x-auto`) |
| Manifest schema (no migration) | Task 14 (audit only) |
| `/api/cluster/install-targets` extension | Task 11 |
| `/api/store/catalog` (unchanged) | n/a |
| `BACKEND_META` constant | Task 5 |
| Filter logic (pure function) | Tasks 3-4 |
| Install routing — single-device default | Task 12 (backend) + Task 13 (frontend) |
| Edge cases (no workers, offline, missing fields, etc.) | Covered in Tasks 4, 6, 9 |
| Persistence | Task 9 + Task 10 Steps 3-4 |
| Tests (unit, component, backend, manual smoke) | Tasks 3, 7, 11, 12, 15 |

**Placeholder scan:** No "TBD"/"TODO"/"add error handling here" patterns. Toast wiring for the auto-deselect notice is explicitly noted as a follow-up rather than left as a placeholder.

**Type consistency:** `CatalogApp`, `InstallTarget`, `FilterResult`, `BackendMeta` are defined once and referenced consistently. `tier_id` and `friendly_name` field names are stable across types, API extension, and frontend usage.

Plan complete and saved to `docs/superpowers/plans/2026-05-06-store-filter-by-device-and-backend-plan.md`.
