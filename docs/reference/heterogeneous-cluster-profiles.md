# Heterogeneous cluster profiles

TAOS assumes you will mix hardware. A cheap Orange Pi next to a gaming
RTX card next to a MacBook, next to an old Intel NUC. The scheduler
and placement logic need to understand that *capability is not
uniform*, and users need a way to express "only these workers are
eligible" or "prefer these workers" per agent.

This document describes the profile concept, the built-in profiles we
ship, and how a user or the scheduler picks one.

## What a profile is

A profile is a named bundle of:

- **Worker selectors** — hardware, capability and label rules a worker
  must match (or not match) to be eligible
- **Resource hints** — soft preferences the scheduler uses to rank
  eligible workers (prefer high VRAM, prefer NPU, prefer low load)
- **Default KV quant** — the K/V/boundary defaults to apply if the
  user doesn't override them in the deploy wizard

Profiles are *not* hard-coded worker groups. They are rules evaluated
against the live cluster state. If a user plugs in a new GPU box, it
joins whichever profiles match it without any manual re-labelling.

## Built-in profiles

| Profile | Selector | KV defaults | Intended use |
|---|---|---|---|
| `edge-cpu` | CPU only, `<16GB` RAM | fp16/fp16 | Orange Pi, Raspberry Pi, embedded boxes |
| `edge-npu` | `npu != none` | fp16/fp16 | RK3588, Jetson Nano, Coral, Hailo |
| `consumer-gpu` | GPU, 6-12GB VRAM | q8_0/fp16 | 3060, 4060, 2070, 3060Ti |
| `gaming-gpu` | GPU, >=12GB VRAM | q8_0/turbo3 | 4070+, 3090, 4090, A-series |
| `workstation-gpu` | GPU, >=24GB VRAM | q8_0/turbo3 | A6000, A100, H100, multi-GPU |
| `apple-silicon` | platform=darwin, arch=arm64 | fp16/fp16 | M1/M2/M3/M4 Mac |
| `cloud-only` | `labels.tier == cloud` | n/a | Anthropic / OpenAI / hosted |

A worker can match multiple profiles. A 4090 gaming rig matches
`consumer-gpu`, `gaming-gpu`, AND `workstation-gpu` if it has 24GB.
That is fine — the user picks which profile they want the agent to
*prefer*, not which one the worker belongs to.

## How agents reference profiles

Config-side:

```yaml
agents:
  - name: research-agent
    color: "#abc"
    profile: gaming-gpu        # prefer this profile
    profile_strict: false      # ok to fall back to other eligible workers
```

When `profile_strict: true`, the scheduler will refuse to dispatch to
anything outside the profile — if no matching worker is online, the
agent pauses (subject to `on_worker_failure` policy).

When `profile_strict: false` (default), the scheduler prefers matching
workers but falls through to any worker with the needed capability.

## How the scheduler uses profiles

1. Agent requests dispatch with capability `llm-chat`
2. Scheduler reads the agent's `profile` and `profile_strict` fields
3. Candidates are all online workers advertising `llm-chat`
4. Candidates are filtered to those matching the profile (if strict)
   or ranked so matches come first (if permissive)
5. Inside the matching set, rank by resource hints (free VRAM, load)
6. Dispatch to the best candidate

Step 2-4 is the new part. Steps 5-6 are the existing capability router
from `tinyagentos/cluster/router.py`.

## User experience

In the deploy wizard, the profile selector is a dropdown populated
from:

- Built-in profiles
- User-defined profiles (from the Profiles settings page — planned)
- An "Any worker" default that imposes no filter

Below the dropdown we show a live preview: "3 workers match:
taos-debian-cuda, taos-fresh-test, laptop-mac". If zero workers match,
we show a warning and a link to the Profiles page.

## Custom profiles

A user can define a profile in `config.yaml`:

```yaml
profiles:
  - name: home-lab-fast
    selector:
      hardware:
        ram_mb_min: 16384
        gpu_vram_mb_min: 8192
      capabilities: [llm-chat, embedding]
      labels:
        location: home
    resource_hints:
      prefer: [free_vram_mb, load_inverse]
    kv_defaults:
      k: q8_0
      v: turbo3
      boundary_layers: 0
```

Custom profiles are first-class citizens. They appear in the wizard
dropdown alongside the built-ins, and they are saved / edited via the
Profiles settings page.

## Labels

Workers can carry free-form labels set at install time via
`install-worker.sh --label location=home --label tier=trusted`, or
edited later from the Workers page. Labels are the escape hatch when
the hardware-based selectors are not enough.

Common patterns:

- `location=<room>` — for "route this to the office worker"
- `tier=trusted` — for agents that hold sensitive data
- `owner=<user>` — for multi-user TAOS installs
- `purpose=training` — to exclude training workers from inference

## Open questions

- Should profiles be immutable built-ins or editable by the user? Current
  thinking: built-ins are read-only and cloned-to-edit, so you always
  know what `gaming-gpu` means.
- Should profile matching be strict-by-default? Probably not — most
  users just want "prefer this, fall back if needed", and strict is
  an expert knob.
- How do we surface "profile has zero matches" without scaring users
  when they're first setting up? The wizard preview is the answer, but
  the copy needs work.

These ship as part of the v0.3 scheduler refactor. Tracked in #212.
