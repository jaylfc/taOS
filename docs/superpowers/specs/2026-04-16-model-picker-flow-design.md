# Model Picker Flow — Design Spec

## Overview

Replace the flat model list in the agent creation wizard (Step 2) and the fallback model `<select>` dropdowns (Step 5) with a tiered picker flow. The flow mirrors the add-provider wizard: source first (Local / Worker / Cloud), then provider if multiple exist, then a searchable model list.

## Component Architecture

### New file: `desktop/src/components/ModelPickerFlow.tsx`

A self-contained sub-wizard component that owns its own screen state. Receives models as props — no new API calls inside.

```ts
interface ModelPickerFlowProps {
  models: Model[]        // already fetched by AgentsApp
  modelsLoaded: boolean
  onSelect: (modelId: string, model: Model) => void
  onBack?: () => void    // inline mode: Back on source screen → wizard step 1
  onCancel?: () => void  // modal mode: Cancel on source screen → close modal
}
```

Internal state:
- `screen: 'source' | 'provider' | 'list'`
- `selectedSource: 'local' | 'worker' | 'cloud' | null`
- `selectedProvider: string | null`
- `search: string`

### Existing file: `desktop/src/apps/AgentsApp.tsx`

Step 2 and the fallback section are updated to use the new component. Model fetching logic stays in AgentsApp.

### New file: `desktop/src/components/ModelPickerModal.tsx`

Thin dialog shell that wraps `ModelPickerFlow` for use in the fallback flow. No logic of its own.

## ModelPickerFlow Screens

### Screen 1 — Source

Displays up to three tiles: **Local**, **Worker**, **Cloud**. Only tiles with at least one available model are rendered.

- If only one source has models: auto-select it and advance immediately (skip to Screen 2 or 3)
- Navigation: `onBack` renders as "← Back", `onCancel` renders as "Cancel" — only one will be provided

### Screen 2 — Provider

Shown only when the selected source has multiple workers or multiple cloud providers. Lists worker names or cloud provider names as selectable tiles.

- If only one provider: auto-select and skip to Screen 3
- Navigation: Back returns to Screen 1

### Screen 3 — Model list

Filtered to models matching the selected source and provider.

- Search box at top — case-insensitive substring match on model name or ID
- Scrollable list of model cards (name, ID, source badge)
- Tapping a model calls `onSelect` immediately
- Navigation: Back returns to Screen 2 (or Screen 1 if Screen 2 was skipped)

## Step 2 — Inline Integration

Step 2 renders one of two states:

**No model selected:** Renders `<ModelPickerFlow>` inline with `onBack={() => setStep(1)}`. The wizard's own Back/Next buttons at the bottom are hidden while the picker is active (no model chosen) to prevent nav conflicts.

**Model selected:** Shows a summary card with the model name, source badge, and a "Change" button. Next is enabled. Clicking "Change" resets `selectedModel` to `""` and returns to the picker at Screen 1.

## Fallback Model — Modal Integration

The "Add fallback model" button in Step 5 opens `<ModelPickerModal>`, which renders `<ModelPickerFlow>` with `onCancel` (closes modal) and `onSelect` (appends to `fallbackModels`, closes modal).

Each existing fallback row shows the model name and a remove (×) button. No inline editing — to change a fallback, remove it and add again.

The existing `<select>` dropdowns for fallback models are removed entirely.

## Edge Cases

| Scenario | Behaviour |
|---|---|
| No models at all | Existing empty state shown (no change) |
| Only local models | Source screen skipped, goes straight to model list |
| Only one worker | Provider screen skipped |
| Only one cloud provider | Provider screen skipped |
| Model already selected (Step 2) | Summary card shown, picker not rendered until "Change" clicked |
| Fallback = same as primary model | Not filtered out — user may want redundancy across workers |

## What Changes

- `AgentsApp.tsx` — Step 2 rendering replaced; fallback `<select>` rows replaced with modal trigger; wizard Back/Next visibility logic updated
- New `ModelPickerFlow.tsx`
- New `ModelPickerModal.tsx`
- No changes to API, data fetching, or model store
