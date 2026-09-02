# Wake-budget config

**Status:** Proposed. Implements tsk-kj3hc2.
**Date:** 2026-08-27

## Why

Every scheduled wake is a paid LLM turn. A chatty channel or misconfigured
heartbeat can exhaust a daily budget in minutes. The current heartbeat loop
has no per-agent ceiling, and external agents set their own poll cadence.
This design adds a single, OS-enforced budget that agents cannot override.

## Config model

A new top-level `wake_budget` block in `config.yaml`:

```yaml
wake_budget:
  global_default: 2          # scheduled checks per day (rule 6 fleet default)
  per_agent:                 # agent id -> override
    "agent-42": 5
  per_project:               # project id -> override for project-bound agents
    "proj-abc": 1
```

Resolution order (most specific wins):
1. `per_project[project_id]` when the agent currently holds a claimed task in
   that project, or when its next ready task is assigned to that project
2. `per_agent[agent_id]`
3. `global_default`

The `project_id` is not a stable agent binding. It is resolved at read time
from the agent's current state: the project of the task the agent is actively
holding (`held_task`), falling back to the project of the next ready task
(`list_ready_tasks_for_assignee`), and finally to `None` (global key). This
means the per-project budget applies to the work the agent is doing right now,
not to a configured affiliation.

Resolutions are not currently logged; add structured logging to
`resolve_budget` to make cost attributable.

## Wake types

- **Scheduled** -- heartbeat ticks, routine fires, external poll cadence.
  Counts against the resolved budget. OS blocks further wakes once the
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
  Resolution is not currently logged; add structured logging to
  `resolve_budget` to make cost attributable.
- **Decision** -- dec-sfdooy closed: the fleet default stays at 2/day until
  this ships; per-agent and per-project overrides are opt-in.

## Interim

Rule 6 (2 scheduled checks/day, acked by website-dev + taosmd-dev on the
bus, msgs 2029/2030) remains the fleet default. Existing configs without a
`wake_budget` block inherit the 2/day default automatically via
`load_config()` and `AppConfig`.
