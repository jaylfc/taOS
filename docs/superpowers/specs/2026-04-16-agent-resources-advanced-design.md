# Agent Resources — Advanced Collapsible Design Spec

## Overview

Remove the Resources step from the agent creation wizard entirely. Default memory and CPU to unlimited (null). Move resource controls into a collapsible "Advanced" section at the bottom of the Review step, populated with hardware-aware options fetched from `/api/activity`.

## Wizard Structure

Steps change from 7 to 6 — Resources (formerly Step 3) is removed:

| Index | Step |
|-------|------|
| 0 | Name & Color |
| 1 | Framework |
| 2 | Model |
| 3 | Permissions |
| 4 | Failure Policy |
| 5 | Review |

All step index references in `canNext()`, the STEPS label array, and navigation update accordingly.

## Default Values and API Payload

Memory and CPU state default to `""` (empty string = unlimited).

Deploy payload:
- `memory === ""` → send `memory_limit: null` (omit limit)
- `memory !== ""` → send formatted string e.g. `"2GB"` or `"512MB"` (existing logic)
- `cpus === ""` → send `cpu_limit: null`
- `cpus !== ""` → send `parseInt(cpus)`

The backend already handles `null` limits — no backend changes required.

## Advanced Collapsible in Review Step

### State

```ts
const [showAdvanced, setShowAdvanced] = useState(false);
const [advancedLoaded, setAdvancedLoaded] = useState(false);
const [systemRamMb, setSystemRamMb] = useState<number | null>(null);
const [systemCpuCores, setSystemCpuCores] = useState<number | null>(null);
```

### Lazy fetch

Triggered the first time the Advanced section is expanded (not on wizard open):

```ts
if (!advancedLoaded) {
  fetch("/api/activity", { headers: { Accept: "application/json" } })
    .then(r => r.json())
    .then(data => {
      setSystemRamMb(data?.hardware?.ram_mb ?? null);
      setSystemCpuCores(data?.hardware?.cpu?.cores ?? null);
      setAdvancedLoaded(true);
    })
    .catch(() => setAdvancedLoaded(true)); // fall back to static options
}
```

### Memory options

Powers of 2 in MB: `[256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072]`

Filtered to `<= systemRamMb` when available. Displayed as:
- `< 1024` → `"X MB"`
- `>= 1024` → `"X GB"`

Fallback (no system info): `[256, 512, 1024, 2048, 4096]`

First option is always "Unlimited (default)" with value `""`.

### CPU options

`1` through `systemCpuCores` (or `4` as fallback). First option is "Unlimited (default)" with value `""`.

### Toggle button

```
▸ Advanced settings   (collapsed)
▾ Advanced settings   (expanded)
```

Rendered below the summary table in the Review step. Selecting any option in Advanced immediately updates the `memory` / `cpus` state (reflected in the summary table above without needing to collapse).

### Review summary table

| Row | Value when unlimited | Value when set |
|-----|---------------------|----------------|
| Memory | Unlimited | e.g. "8 GB" |
| CPUs | Unlimited | e.g. "4 Cores" |

Both rows always shown in the summary table.

## What Does Not Change

- KV cache quantization controls remain in the Failure Policy step
- Fallback model section unchanged
- All other wizard steps unchanged
- No backend changes required
