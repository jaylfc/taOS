# Dumby the Dumbo — End-to-End User Simulation

A long-running soak test where a Haiku 4.5 subagent acts as a real taOS user,
drives the PWA via Playwright over LAN, and produces a real creative artifact
("Dumby the Dumbo" — a kids' book) using five specialised in-taOS agents that
hand work off and critique each other.

The point is not the book. The point is to find every place taOS still trips
up an autonomous user across a multi-hour, multi-feature workflow.

## What it exercises

- Login flow, session persistence
- Project creation + browsing
- Project Members tab — attaches the 6 existing Pi agents (one per
  framework: openclaw, hermes, smolagents, langroid, pocketflow,
  openai-agents-sdk) so the test exercises every framework end-to-end
- Agent shell access (LXC tool installs)
- Project chat + cross-agent handoff (A2A)
- Images app (cover + scene illustrations)
- Files app (HTML web page, marketing.md, social.md)
- Inter-agent review/critique loops
- Resumable state across multiple sessions

## Architecture

```
┌─────────────────────────────────────────────┐
│ Mac (this repo)                             │
│                                             │
│  ┌───────────────────────────────────────┐  │
│  │ Controller (Claude Code session)      │  │
│  │  · dispatches Haiku user-sim runs     │  │
│  │  · on BLOCKED: dispatches Sonnet fix  │  │
│  │  · merges fixes, resumes              │  │
│  └────────────────┬──────────────────────┘  │
│                   │ Agent tool              │
│  ┌────────────────▼──────────────────────┐  │
│  │ Haiku user-sim subagent (~30 min)     │  │
│  │  · Playwright MCP → driver browser    │  │
│  │  · taOS REST helper for bulk ops      │  │
│  │  · reads/writes runs/<ts>/state.json  │  │
│  │  · returns {status, blocks[], …}      │  │
│  └────────────────┬──────────────────────┘  │
└───────────────────┼─────────────────────────┘
                    │ HTTP over LAN
┌───────────────────▼─────────────────────────┐
│ Pi taOS (192.168.6.123:6969)                │
│  · Project: Dumby the Dumbo                 │
│  · 6 existing agents on kilo-auto/free      │
│    (one per framework, attached as Members) │
└─────────────────────────────────────────────┘
```

## Layout

| File                  | Purpose                                                  |
|-----------------------|----------------------------------------------------------|
| `runner-prompt.md`    | Instructions loaded into the Haiku user-sim subagent     |
| `roles.md`            | Role mapping: 6 existing Pi agents → 6 roles + briefs    |
| `taos_setup.md`       | Pi URL, login flow, env file path                        |
| `state.example.json`  | Schema reference for the resumable run state             |
| `runs/`               | Gitignored; one directory per run (state, logs, exports) |

## Starting / resuming a run

The controller (Claude Code session driving this) handles the loop. Each
session works one of these:

1. **Fresh run** — create `runs/<utc-iso>/` with a clean `state.json` derived
   from `state.example.json`, then dispatch the Haiku subagent with the
   `runner-prompt.md` and the run directory.

2. **Resume** — point the Haiku subagent at an existing `runs/<ts>/` whose
   `state.json` has `status: IN_PROGRESS`.

3. **Fix-then-resume** — when a previous run returned `status: BLOCKED`, the
   controller dispatches a Sonnet fix subagent against the project repo with
   the block details, lands the fix, then resumes against the same run dir.

## State file (`state.json`)

See `state.example.json` for the full schema. Key fields:

- `status` — `pending | in_progress | blocked | done`
- `phase` — current high-level phase (`login`, `project_create`, …, `done`)
- `completed_steps` — append-only list of step IDs
- `agents` — per-agent record (name, model, persona, last activity)
- `artifacts` — paths to story chapters, images, web page, marketing docs
- `blocks` — `{step, severity, summary, evidence}` whenever the sim hits a wall
- `transcript` — relative path to the append-only run log

## Costs

- Haiku 4.5 controller: bounded per-session by max_tokens + tool-call cap
- kilo-auto/free for the in-taOS agents: no spend (per project test policy)
- Wall-clock cap per Haiku session: ~30 min

## Why no Python harness

The Haiku subagent is dispatched directly via the Claude Code Agent tool with
the Playwright MCP browser tools and Bash for HTTP/file ops. No separate
Python runner — keeping the moving parts down means fewer places for the
test infra itself to fail.
