# Model Picker Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat model list in the agent creation wizard and the fallback `<select>` dropdowns with a tiered picker flow: source (Local / Worker / Cloud) → provider (if multiple) → searchable model list.

**Architecture:** A new `ModelPickerFlow` component owns all picker state and sub-screen navigation; it is rendered inline for Step 2 and wrapped in a `ModelPickerModal` dialog for fallback model selection. `AgentsApp.tsx` stays the data owner — models are already fetched there and passed as props.

**Tech Stack:** React, TypeScript, Tailwind CSS, Lucide icons, Vitest (build verification only — no DOM testing library installed)

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Create | `desktop/src/components/ModelPickerFlow.tsx` | Tiered picker: source → provider → list + search |
| Create | `desktop/src/components/ModelPickerModal.tsx` | Thin dialog wrapper for fallback use |
| Modify | `desktop/src/apps/AgentsApp.tsx` | Step 2 inline picker, fallback modal, CLOUD_TYPES fix, type rename |

---

## Task 1: Create ModelPickerFlow.tsx

**Files:**
- Create: `desktop/src/components/ModelPickerFlow.tsx`

- [ ] **Step 1: Create the file with the full component implementation**

```tsx
import { useState, useEffect, useCallback } from "react";
import { ChevronLeft, Monitor, Server, Cloud, Search, X } from "lucide-react";
import { HOST_BADGE_CLASS } from "@/lib/models";

export interface AgentModel {
  id: string;
  name: string;
  host?: string;
  hostKind?: "controller" | "worker" | "cloud";
}

interface Props {
  models: AgentModel[];
  modelsLoaded: boolean;
  onSelect: (modelId: string, model: AgentModel) => void;
  onBack?: () => void;    // inline mode: shown on source screen as "Back"
  onCancel?: () => void;  // modal mode: shown on source screen as "Cancel"
}

type Screen = "source" | "provider" | "list";
type Source = "local" | "worker" | "cloud";

const SOURCE_META: Record<Source, { label: string; icon: React.ReactNode; desc: string }> = {
  local:  { label: "Local",  icon: <Monitor size={18} />, desc: "Downloaded on this device" },
  worker: { label: "Worker", icon: <Server  size={18} />, desc: "Hosted on cluster workers"  },
  cloud:  { label: "Cloud",  icon: <Cloud   size={18} />, desc: "Cloud provider API"          },
};

export function ModelPickerFlow({ models, modelsLoaded, onSelect, onBack, onCancel }: Props) {
  const [screen, setScreen]                   = useState<Screen>("source");
  const [selectedSource, setSelectedSource]   = useState<Source | null>(null);
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null);
  const [search, setSearch]                   = useState("");

  // Partition by source
  const localModels  = models.filter(m => m.hostKind === "controller" || !m.hostKind);
  const workerModels = models.filter(m => m.hostKind === "worker");
  const cloudModels  = models.filter(m => m.hostKind === "cloud");

  const availableSources: Source[] = [
    ...(localModels.length  > 0 ? ["local"  as Source] : []),
    ...(workerModels.length > 0 ? ["worker" as Source] : []),
    ...(cloudModels.length  > 0 ? ["cloud"  as Source] : []),
  ];

  const workerProviders = [...new Set(workerModels.map(m => m.host ?? "unknown"))];
  const cloudProviders  = [...new Set(cloudModels.map(m =>  m.host ?? "unknown"))];

  const goToProvider = useCallback((source: Source) => {
    const providers = source === "worker" ? workerProviders : cloudProviders;
    if (providers.length === 1) {
      setSelectedProvider(providers[0]);
      setScreen("list");
    } else {
      setScreen("provider");
    }
  }, [workerProviders, cloudProviders]);

  const handleSourceSelect = useCallback((source: Source) => {
    setSelectedSource(source);
    setSearch("");
    if (source === "local") {
      setSelectedProvider(null);
      setScreen("list");
    } else {
      goToProvider(source);
    }
  }, [goToProvider]);

  // Auto-select if only one source has models
  useEffect(() => {
    if (modelsLoaded && availableSources.length === 1) {
      handleSourceSelect(availableSources[0]);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelsLoaded]);

  const handleProviderSelect = (provider: string) => {
    setSelectedProvider(provider);
    setSearch("");
    setScreen("list");
  };

  const handleBack = () => {
    if (screen === "source") {
      onBack?.();
      onCancel?.();
    } else if (screen === "provider") {
      setSelectedSource(null);
      setScreen("source");
    } else {
      // list → provider (if there were multiple providers) or source
      const providers = selectedSource === "worker" ? workerProviders : cloudProviders;
      if (selectedSource !== "local" && providers.length > 1) {
        setSelectedProvider(null);
        setScreen("provider");
      } else {
        setSelectedSource(null);
        setScreen("source");
      }
    }
  };

  // Models visible in the list screen, filtered by source/provider then search
  const listModels = models
    .filter(m => {
      if (selectedSource === "local")  return m.hostKind === "controller" || !m.hostKind;
      if (selectedSource === "worker") return m.hostKind === "worker" && m.host === selectedProvider;
      if (selectedSource === "cloud")  return m.hostKind === "cloud"  && m.host === selectedProvider;
      return false;
    })
    .filter(m => {
      if (!search) return true;
      const q = search.toLowerCase();
      return m.name.toLowerCase().includes(q) || m.id.toLowerCase().includes(q);
    });

  /* ── Source screen ─────────────────────────────── */
  if (screen === "source") {
    const exitLabel = onCancel ? "Cancel" : "Back";
    return (
      <div className="space-y-2">
        {(onBack || onCancel) && (
          <button
            onClick={handleBack}
            className="flex items-center gap-1 text-xs text-shell-text-tertiary hover:text-shell-text mb-3 transition-colors"
          >
            <ChevronLeft size={14} />
            {exitLabel}
          </button>
        )}
        <span className="block text-xs text-shell-text-secondary mb-2">Where is the model?</span>
        {!modelsLoaded && (
          <p className="text-xs text-shell-text-tertiary py-2">Loading models…</p>
        )}
        {modelsLoaded && availableSources.length === 0 && (
          <p className="text-xs text-shell-text-tertiary py-2">No models available.</p>
        )}
        <div className="grid grid-cols-1 gap-2">
          {availableSources.map(source => {
            const { label, icon, desc } = SOURCE_META[source];
            return (
              <button
                key={source}
                onClick={() => handleSourceSelect(source)}
                className="w-full text-left px-4 py-3 rounded-lg border border-white/5 bg-shell-bg-deep hover:bg-white/5 transition-colors flex items-center gap-3"
              >
                <span className="text-accent shrink-0">{icon}</span>
                <div>
                  <div className="text-sm font-medium">{label}</div>
                  <div className="text-xs text-shell-text-tertiary">{desc}</div>
                </div>
              </button>
            );
          })}
        </div>
      </div>
    );
  }

  /* ── Provider screen ───────────────────────────── */
  if (screen === "provider") {
    const providers = selectedSource === "worker" ? workerProviders : cloudProviders;
    const heading   = selectedSource === "worker" ? "Select worker" : "Select provider";
    return (
      <div className="space-y-2">
        <button
          onClick={handleBack}
          className="flex items-center gap-1 text-xs text-shell-text-tertiary hover:text-shell-text mb-3 transition-colors"
        >
          <ChevronLeft size={14} />
          Back
        </button>
        <span className="block text-xs text-shell-text-secondary mb-2">{heading}</span>
        <div className="grid grid-cols-1 gap-2">
          {providers.map(provider => (
            <button
              key={provider}
              onClick={() => handleProviderSelect(provider)}
              className="w-full text-left px-4 py-3 rounded-lg border border-white/5 bg-shell-bg-deep hover:bg-white/5 transition-colors"
            >
              <div className="text-sm font-medium">{provider}</div>
            </button>
          ))}
        </div>
      </div>
    );
  }

  /* ── Model list screen ─────────────────────────── */
  return (
    <div className="space-y-2">
      <button
        onClick={handleBack}
        className="flex items-center gap-1 text-xs text-shell-text-tertiary hover:text-shell-text mb-1 transition-colors"
      >
        <ChevronLeft size={14} />
        Back
      </button>
      <div className="relative mb-2">
        <Search size={14} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-shell-text-tertiary pointer-events-none" />
        <input
          type="text"
          placeholder="Search models…"
          value={search}
          onChange={e => setSearch(e.target.value)}
          className="w-full pl-8 pr-8 h-8 rounded-lg border border-white/10 bg-shell-bg-deep text-sm text-shell-text placeholder:text-shell-text-tertiary focus:outline-none focus:border-accent/40"
          autoFocus
        />
        {search && (
          <button
            onClick={() => setSearch("")}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-shell-text-tertiary hover:text-shell-text"
            aria-label="Clear search"
          >
            <X size={12} />
          </button>
        )}
      </div>
      {listModels.length === 0 && (
        <p className="text-xs text-shell-text-tertiary py-4 text-center">No models match your search.</p>
      )}
      {listModels.map(m => (
        <button
          key={`${m.hostKind ?? "?"}:${m.host ?? "?"}:${m.id}`}
          onClick={() => onSelect(m.id, m)}
          className="w-full text-left px-4 py-3 rounded-lg border border-white/5 bg-shell-bg-deep hover:bg-white/5 transition-colors"
        >
          <div className="flex items-center gap-1.5 min-w-0">
            <div className="text-sm font-medium truncate">{m.name}</div>
            {m.host && m.hostKind !== "controller" && (
              <span className={HOST_BADGE_CLASS} title={`Hosted on ${m.host}`}>{m.host}</span>
            )}
          </div>
          <div className="text-xs text-shell-text-tertiary">{m.id}</div>
        </button>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd /path/to/tinyagentos/desktop && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors referencing `ModelPickerFlow.tsx`.

- [ ] **Step 3: Commit**

```bash
cd desktop
git add src/components/ModelPickerFlow.tsx
git commit -m "feat(agents): add ModelPickerFlow tiered model picker component"
```

---

## Task 2: Create ModelPickerModal.tsx

**Files:**
- Create: `desktop/src/components/ModelPickerModal.tsx`

- [ ] **Step 1: Create the file**

```tsx
import { X } from "lucide-react";
import { Button } from "@/components/ui";
import { ModelPickerFlow, type AgentModel } from "./ModelPickerFlow";

interface Props {
  open: boolean;
  onClose: () => void;
  models: AgentModel[];
  modelsLoaded: boolean;
  onSelect: (modelId: string, model: AgentModel) => void;
  title?: string;
}

export function ModelPickerModal({
  open,
  onClose,
  models,
  modelsLoaded,
  onSelect,
  title = "Select Model",
}: Props) {
  if (!open) return null;

  return (
    <div
      className="absolute inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      style={{
        paddingTop: "calc(1rem + env(safe-area-inset-top, 0px))",
        paddingBottom: "calc(1rem + env(safe-area-inset-bottom, 0px))",
      }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div
        className="w-full max-w-md max-h-full min-h-0 bg-shell-surface rounded-xl border border-white/10 shadow-2xl overflow-hidden flex flex-col"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/5 shrink-0">
          <h2 className="text-sm font-semibold">{title}</h2>
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7"
            onClick={onClose}
            aria-label="Close"
          >
            <X size={16} />
          </Button>
        </div>
        <div className="px-5 py-5 flex-1 min-h-0 overflow-y-auto">
          <ModelPickerFlow
            models={models}
            modelsLoaded={modelsLoaded}
            onSelect={(id, m) => {
              onSelect(id, m);
              onClose();
            }}
            onCancel={onClose}
          />
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd desktop && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors.

- [ ] **Step 3: Commit**

```bash
git add src/components/ModelPickerModal.tsx
git commit -m "feat(agents): add ModelPickerModal wrapper for fallback model selection"
```

---

## Task 3: Update AgentsApp — imports, types, and CLOUD_TYPES fix

**Files:**
- Modify: `desktop/src/apps/AgentsApp.tsx`

This task touches the top of the file only: imports, the local `Model` interface, and the CLOUD_TYPES constant inside the fetch block.

- [ ] **Step 1: Update the import from `@/lib/models`**

Find this block near line 4:
```ts
import {
  fetchClusterWorkers,
  workersToAggregated,
  HOST_BADGE_CLASS,
} from "@/lib/models";
```

Replace with:
```ts
import {
  fetchClusterWorkers,
  workersToAggregated,
  HOST_BADGE_CLASS,
  CLOUD_PROVIDER_TYPES,
} from "@/lib/models";
```

- [ ] **Step 2: Add imports for the new components**

After the existing UI component imports (around line 22), add:
```ts
import { ModelPickerFlow, type AgentModel } from "@/components/ModelPickerFlow";
import { ModelPickerModal } from "@/components/ModelPickerModal";
```

- [ ] **Step 3: Remove the local `Model` interface and add a type alias**

Find (around line 51):
```ts
interface Model {
  id: string;
  name: string;
  host?: string;
  hostKind?: "controller" | "worker" | "cloud";
}
```

Replace with:
```ts
// AgentModel is defined and exported from ModelPickerFlow
type Model = AgentModel;
```

> This keeps all existing `Model` references in `AgentsApp` working without a rename sweep. The alias will be removed in a follow-up if desired.

- [ ] **Step 4: Fix the local CLOUD_TYPES constant**

Find (around line 495):
```ts
const CLOUD_TYPES = ["openai", "anthropic"];
for (const p of (Array.isArray(providers) ? providers : [])) {
  if (!CLOUD_TYPES.includes(p.type)) continue;
```

Replace with:
```ts
for (const p of (Array.isArray(providers) ? providers : [])) {
  if (!(CLOUD_PROVIDER_TYPES as readonly string[]).includes(p.type)) continue;
```

- [ ] **Step 5: Verify TypeScript compiles**

```bash
cd desktop && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add src/apps/AgentsApp.tsx
git commit -m "refactor(agents): import AgentModel type and CLOUD_PROVIDER_TYPES from shared modules"
```

---

## Task 4: Update AgentsApp — Step 2 inline picker

**Files:**
- Modify: `desktop/src/apps/AgentsApp.tsx`

- [ ] **Step 1: Add fallback modal open state near the other Step 5b state (around line 347)**

Find:
```ts
const [fallbackModels, setFallbackModels] = useState<string[]>([]);
```

Replace with:
```ts
const [fallbackModels, setFallbackModels] = useState<string[]>([]);
const [fallbackModelOpen, setFallbackModelOpen] = useState(false);
```

- [ ] **Step 2: Replace the Step 2 rendering block**

Find this entire block (around lines 794–858):
```tsx
          {/* Step 2: Model */}
          {step === 2 && (
            <div className="space-y-2">
              <span className="block text-xs text-shell-text-secondary mb-2">Select Model</span>
              {modelsLoaded && models.length === 0 ? (
                <div className="flex flex-col items-center justify-center gap-3 py-8 px-4 text-center rounded-lg border border-white/5 bg-shell-bg-deep">
                  <div className="w-12 h-12 rounded-xl flex items-center justify-center bg-accent/10">
                    <Download size={20} className="text-accent" />
                  </div>
                  <div>
                    <p className="text-sm font-medium text-shell-text">No models available.</p>
                    <p className="text-xs text-shell-text-tertiary mt-1">
                      No models downloaded on the controller, hosted on cluster workers, or provided by cloud providers.
                    </p>
                  </div>
                  <Button size="sm" onClick={openModelsApp}>
                    <Download size={13} />
                    Get more models
                  </Button>
                </div>
              ) : (
                <>
                  {models.map((m) => {
                    const showHost = m.host && m.hostKind !== "controller";
                    const key = `${m.hostKind ?? "?"}:${m.host ?? "?"}:${m.id}`;
                    return (
                      <button
                        key={key}
                        onClick={() => setSelectedModel(m.id)}
                        className={`w-full text-left px-4 py-3 rounded-lg border transition-colors ${
                          selectedModel === m.id
                            ? "border-accent bg-accent/10"
                            : "border-white/5 bg-shell-bg-deep hover:bg-white/5"
                        }`}
                      >
                        <div className="flex items-center gap-1.5 min-w-0">
                          <div className="text-sm font-medium truncate">{m.name}</div>
                          {showHost && (
                            <span
                              className={HOST_BADGE_CLASS}
                              title={`Hosted on ${m.host}`}
                            >
                              {m.host}
                            </span>
                          )}
                        </div>
                        <div className="text-xs text-shell-text-tertiary">{m.id}</div>
                      </button>
                    );
                  })}
                  {models.length > 0 && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={openModelsApp}
                      className="w-full mt-2"
                    >
                      <Download size={13} />
                      Get more models
                    </Button>
                  )}
                </>
              )}
            </div>
          )}
```

Replace with:
```tsx
          {/* Step 2: Model */}
          {step === 2 && (
            <div className="space-y-2">
              {selectedModel ? (
                /* Summary card — shown after a model is picked */
                <div>
                  <span className="block text-xs text-shell-text-secondary mb-2">Selected Model</span>
                  <div className="px-4 py-3 rounded-lg border border-accent/30 bg-accent/5 flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="flex items-center gap-1.5 min-w-0">
                        <div className="text-sm font-medium truncate">
                          {models.find(m => m.id === selectedModel)?.name ?? selectedModel}
                        </div>
                        {(() => {
                          const m = models.find(mo => mo.id === selectedModel);
                          return m?.host && m.hostKind !== "controller" ? (
                            <span className={HOST_BADGE_CLASS}>{m.host}</span>
                          ) : null;
                        })()}
                      </div>
                      <div className="text-xs text-shell-text-tertiary mt-0.5">{selectedModel}</div>
                    </div>
                    <button
                      onClick={() => setSelectedModel("")}
                      className="text-xs text-shell-text-tertiary hover:text-shell-text shrink-0 mt-0.5 transition-colors"
                    >
                      Change
                    </button>
                  </div>
                </div>
              ) : (
                /* Tiered picker — source → provider → list */
                <ModelPickerFlow
                  models={models}
                  modelsLoaded={modelsLoaded}
                  onSelect={(id) => setSelectedModel(id)}
                  onBack={() => setStep(1)}
                />
              )}
            </div>
          )}
```

- [ ] **Step 3: Hide the wizard footer while the picker is active**

Find (around line 1125):
```tsx
        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-3 border-t border-white/5 shrink-0">
```

Replace with:
```tsx
        {/* Footer — hidden while the inline model picker is active (has its own nav) */}
        {!(step === 2 && !selectedModel) && (
        <div className="flex items-center justify-between px-5 py-3 border-t border-white/5 shrink-0">
```

Then find the closing `</div>` of the footer (after the Deploy button, around line 1156):
```tsx
        </div>
      </div>
    </div>
  );
```

The outermost `</div>` on the first line closes the footer. Add the closing `)` for the conditional after it:
```tsx
        </div>
        )}
      </div>
    </div>
  );
```

- [ ] **Step 4: Verify TypeScript compiles**

```bash
cd desktop && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors.

- [ ] **Step 5: Start the dev server and visually verify Step 2**

```bash
cd desktop && npm run dev
```

Open the agents app → click Deploy Agent → advance to Step 2.

Verify:
- Source tiles appear (Local / Worker / Cloud) — only tiles with models
- Selecting a source navigates to provider (if >1) or model list
- Selecting a model shows the summary card; wizard footer appears with Next enabled
- "Change" resets to source screen; footer hides again
- Back on source screen goes to Step 1 (Framework)

- [ ] **Step 6: Commit**

```bash
git add src/apps/AgentsApp.tsx
git commit -m "feat(agents): tiered model picker inline in deploy wizard step 2"
```

---

## Task 5: Update AgentsApp — fallback model modal

**Files:**
- Modify: `desktop/src/apps/AgentsApp.tsx`

- [ ] **Step 1: Replace the fallback model rows and button in Step 5**

Find this block (around lines 956–999):
```tsx
              <div>
                <Label className="mb-1.5 block">
                  Fallback models{" "}
                  <span className="font-normal text-shell-text-tertiary">(optional, in priority order)</span>
                </Label>
                <div className="space-y-1.5">
                  {fallbackModels.map((m, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <select
                        value={m}
                        onChange={(e) => {
                          const updated = [...fallbackModels];
                          updated[i] = e.target.value;
                          setFallbackModels(updated);
                        }}
                        className="flex-1 h-8 rounded-lg border border-white/10 bg-shell-bg-deep px-2 text-sm text-shell-text"
                        aria-label={`Fallback model ${i + 1}`}
                      >
                        <option value="">-- pick a model --</option>
                        {models.filter((mo) => mo.id !== selectedModel).map((mo) => (
                          <option key={mo.id} value={mo.id}>{mo.name}</option>
                        ))}
                      </select>
                      <button
                        onClick={() => setFallbackModels(fallbackModels.filter((_, j) => j !== i))}
                        className="text-shell-text-tertiary hover:text-red-400 transition-colors"
                        aria-label={`Remove fallback model ${i + 1}`}
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ))}
                  {modelsLoaded && models.length > 1 && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setFallbackModels([...fallbackModels, ""])}
                      className="w-full"
                    >
                      <Plus size={13} />
                      Add fallback model
                    </Button>
                  )}
                </div>
              </div>
```

Replace with:
```tsx
              <div>
                <Label className="mb-1.5 block">
                  Fallback models{" "}
                  <span className="font-normal text-shell-text-tertiary">(optional, in priority order)</span>
                </Label>
                <div className="space-y-1.5">
                  {fallbackModels.filter(Boolean).map((m, i) => (
                    <div key={i} className="flex items-center gap-2 px-3 py-2 rounded-lg border border-white/5 bg-shell-bg-deep">
                      <span className="flex-1 text-sm truncate">
                        {models.find(mo => mo.id === m)?.name ?? m}
                      </span>
                      <button
                        onClick={() => setFallbackModels(prev => prev.filter((_, j) => j !== i))}
                        className="text-shell-text-tertiary hover:text-red-400 transition-colors"
                        aria-label={`Remove fallback model ${i + 1}`}
                      >
                        <X size={14} />
                      </button>
                    </div>
                  ))}
                  {modelsLoaded && models.length > 1 && (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => setFallbackModelOpen(true)}
                      className="w-full"
                    >
                      <Plus size={13} />
                      Add fallback model
                    </Button>
                  )}
                </div>
                <ModelPickerModal
                  open={fallbackModelOpen}
                  onClose={() => setFallbackModelOpen(false)}
                  models={models}
                  modelsLoaded={modelsLoaded}
                  title="Add Fallback Model"
                  onSelect={(id) => setFallbackModels(prev => [...prev, id])}
                />
              </div>
```

- [ ] **Step 2: Verify TypeScript compiles**

```bash
cd desktop && npx tsc --noEmit 2>&1 | head -30
```

Expected: no errors.

- [ ] **Step 3: Start the dev server and visually verify fallback flow**

```bash
cd desktop && npm run dev
```

Open Deploy Agent → advance to Step 5 (Failure Policy) → set policy to "Fallback".

Verify:
- "Add fallback model" button opens `ModelPickerModal`
- The modal shows the same source → provider → model list flow
- Cancel button on source screen closes the modal without adding
- Selecting a model closes the modal and adds the model name to the list
- The × button on a fallback row removes it
- Multiple fallbacks can be added in order

- [ ] **Step 4: Commit**

```bash
git add src/apps/AgentsApp.tsx
git commit -m "feat(agents): replace fallback model selects with tiered ModelPickerModal"
```

---

## Self-Review

**Spec coverage:**
- ✅ Source → provider → model list + search flow
- ✅ Auto-skip source screen when only one source
- ✅ Auto-skip provider screen when only one provider
- ✅ Inline in Step 2 with summary card + Change button
- ✅ Wizard footer hidden while picker is active
- ✅ Back on source screen → wizard step 1
- ✅ Fallback modal with Cancel instead of Back on source screen
- ✅ Fallback flow reuses same component
- ✅ CLOUD_TYPES expanded to use CLOUD_PROVIDER_TYPES (includes openrouter, kilocode)

**Placeholder scan:** None — all code is complete.

**Type consistency:**
- `AgentModel` defined in Task 1, re-exported via `type AgentModel` in Task 2's import, aliased as `type Model = AgentModel` in Task 3 — all references consistent
- `fallbackModelOpen` / `setFallbackModelOpen` introduced in Task 4 Step 1, used in Task 5 Step 1 — consistent
- `ModelPickerFlow` props (`models`, `modelsLoaded`, `onSelect`, `onBack`, `onCancel`) match across Tasks 1, 4, and 5
- `ModelPickerModal` props match between Task 2 definition and Task 5 usage
