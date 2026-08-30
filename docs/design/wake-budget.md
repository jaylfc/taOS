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
1. `per_project[project_id]` when the wake's task carries that project
2. `per_agent[agent_id]`
3. `global_default`

The agent dict never carries a `project_id` in production; the project is
taken from the task the heartbeat fires on. Readers aggregate consumption
across every per-project key for an agent, and resolve the budget to the
most restrictive applicable `per_project` override. Every resolution is
logged at debug level in `resolve_budget` so cost is attributable.

## Wake types

- **Scheduled** -- heartbeat ticks and external poll cadence. Counts against
  the resolved budget. OS blocks further wakes once the budget is exhausted and
  surfaces the exhaustion to the agent.
  Routine fires (`routine_runner.py`) auto-create a task on the project board
  and best-effort wake the assignee, but they are NOT yet gated by `can_wake()`
  nor charged against the budget. A future change should wire the routine runner
  through the same gate so routine-driven wakes are bounded.

## OS enforcement

The wake-gate lives in `tinyagentos/wake_budget.py`. The heartbeat loop
(`agent_heartbeat.py`) calls `can_wake()` before firing. An agent cannot exceed
its budget by misconfiguring itself because the gate reads the OS config, not any
agent-supplied value. Routine fires (`routine_runner.py`) are not yet wired to
this gate -- see Wake types above.

Daily consumption is persisted in `data_dir/wake_budget.json` and reset
automatically by date rollover.

## Surfaces

- **Agent wake-budget endpoint** -- `GET /api/agents/{name}/wake-budget`
  returns the resolved wake budget, today's consumption, remaining, next
  scheduled wake epoch, and the date. Consumption is aggregated across every
  per-project key the heartbeat has charged for the agent, so the reported
  total matches what enforcement actually wrote.
- **Observatory** -- `/api/observatory/wake-budget` returns each agent's
  `budget`, `consumed`, `remaining`, and `next_wake_epoch` for the current day.
- **Decision** -- dec-sfdooy closed: the fleet default stays at 2/day until
  this ships; per-agent and per-project overrides are opt-in.

## Interim

Rule 6 (2 scheduled checks/day, acked by website-dev + taosmd-dev on the
bus, msgs 2029/2030) remains the fleet default. Existing configs without a
`wake_budget` block inherit the 2/day default automatically via
`normalize_agent`.
