# Wake-budget config

**Status:** Implemented. Implements tsk-jgz4kr.
**Date:** 2026-08-27 (semantics fixed 2026-09-02)

## Why

Every scheduled wake is a paid LLM turn. A chatty channel or misconfigured
heartbeat can exhaust a daily budget in minutes. The current heartbeat loop
has no per-agent ceiling, and external agents set their own poll cadence.
This design adds a single, OS-enforced budget that agents cannot override.

## Semantics (per-agent, fixed tsk-jgz4kr)

The budget is per-agent, not per-project. `global_default: 2` means 2 scheduled
checks per day for the agent, summed across all projects. The heartbeat charges
one key per project (`{agent_id}:{project_id}`) in `wake_budget.json`; the
reader and enforcer sum all of the agent's project keys before comparing to the
budget. This matches TOKEN-DISCIPLINE rule 6 (2/day per agent) and prevents the
blind read that occurred when all the agent's tasks were closed.

## Config model

A new top-level `wake_budget` block in `config.yaml`:

```yaml
wake_budget:
  global_default: 2          # scheduled checks per day per agent (rule 6 fleet default)
  per_agent:                 # agent id -> override
    "agent-42": 5
  per_project:               # (retained in config schema but not applied by reader/enforcer;
                             #  per-agent semantics means project_id is not a budget axis)
    "proj-abc": 1
```

Resolution order for the agent-level budget:
1. `per_agent[agent_id]` when set
2. `global_default`

The `project_id` in `wake_budget.json` keys is a storage detail (one key per
project the agent was charged for); it is **not** used to resolve the budget at
read or enforcement time. This means closing all the agent's tasks cannot make
the reader fall through to an untouched `agent:global` key and report
`consumed=0` while the enforcer charged under project keys.

Resolutions are not currently logged; add structured logging to
`resolve_budget` to make cost attributable.

## Wake types

- **Scheduled** -- heartbeat ticks, routine fires, external poll cadence.
  Counts against the agent's total daily budget. OS blocks further wakes once the
  budget is exhausted and surfaces the exhaustion to the agent.

## OS enforcement

The wake-gate lives in `tinyagentos/wake_budget.py`. The heartbeat loop
(`agent_heartbeat.py`) calls `can_wake()` before firing. An agent cannot
exceed its budget by misconfiguring itself because the gate reads the OS
config, not any agent-supplied value.

Daily consumption is persisted in `data_dir/wake_budget.json` and reset
automatically by date rollover.

Routine fires are not yet gated by `can_wake()`; `routine_runner.py` does
not consult the wake budget. That is a deliberate follow-up.

## Surfaces

- **Agent settings UI** -- `/api/taos-agent/config` does not yet include the
  resolved wake budget; that surface is a follow-up.
- **Observatory** -- `/api/observatory/wake-budget` returns each agent's
  `budget`, `consumed`, `remaining`, and `next_wake_epoch` for the current day.
  `consumed` is the sum of all the agent's project keys, so the figure is
  accurate even when the agent holds no current task. Resolution is not
  currently logged; add structured logging to `resolve_budget` to make cost
  attributable.
- **Decision** -- dec-sfdooy closed: the fleet default stays at 2/day until
  this ships; per-agent overrides are opt-in.

## Interim

Rule 6 (2 scheduled checks/day, acked by website-dev + taosmd-dev on the
bus, msgs 2029/2030) remains the fleet default. Existing configs without a
`wake_budget` block inherit the 2/day default automatically via
`load_config()` and `AppConfig`.
