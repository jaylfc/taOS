# Graceful Lifecycle — Design Spec

## Problem

Any path that stops an agent or the controller can lose work today:
- Auto-update pulls code but leaves the old process running until someone SSHes in and restarts.
- `POST /api/agents/{name}/pause` flips a flag with no chance for the agent to finish current work or save context.
- Controller SIGTERM (systemd stop, Ctrl-C, `docker stop`) tears down the app without warning agents.
- System shutdown / reboot kills everything at once; in-flight LLM responses, pending memory writes, and session context are discarded.

Average users can't recover from these. We need a single orchestrator every stop path flows through.

## Design

### Orchestrator

New module `tinyagentos/restart_orchestrator.py` exposing one primitive:

```python
async def prepare(scope: Literal["all"] | list[str], reason: Reason) -> PrepareReport
```

- `reason`: `"update" | "pause" | "stop" | "controller-shutdown" | "system-shutdown"` — written into each agent's resume note so it knows why it was paused.
- Runs per-agent preparation in parallel, each wrapped in `asyncio.wait_for(300)`.
- Per-agent timeout: **300 seconds**. Generous on purpose.
- Returns `PrepareReport = dict[agent_name, {status, duration_s, note_path}]` where status is `ready | timeout | error`.

### Per-agent prepare sequence (inside the agent container)

New agent-side endpoint `POST /prepare-for-shutdown`:

1. Set `accepting=False` — reject new jobs with 503.
2. Wait for any in-flight job to finish (bounded by the 300s outer deadline).
3. Flush outstanding memory writes to qmd synchronously.
4. Write `resume_note.json` under the agent's memory dir with:
   ```json
   {
     "reason": "update",
     "paused_at": 1776140000,
     "last_user_msg": "...",
     "in_progress_task": "...",
     "next_step_hint": "...",
     "context_snapshot": { ... }
   }
   ```
5. Return `{"status": "ready", "note_path": "..."}`.

### Triggers

Every path that currently stops an agent or the controller goes through `orchestrator.prepare()` first:

| Trigger | Entry point | Scope |
|---|---|---|
| Auto-update restart | `POST /api/system/restart/prepare` | `"all"` |
| Manual agent pause | `POST /api/agents/{name}/pause` | `[name]` |
| Manual agent stop | `POST /api/agents/{name}/stop` | `[name]` |
| Controller SIGTERM | FastAPI lifespan shutdown hook | `"all"` |
| System shutdown / reboot | `taos-pre-shutdown.service` systemd unit | `"all"` |

### Systemd integration

Two pieces:

**1. Main service** (`tinyagentos.service`):
```
ExecStop=/usr/local/bin/taos-graceful-stop
TimeoutStopSec=45
```

**2. Pre-shutdown hook** (`/etc/systemd/system/taos-pre-shutdown.service`):
```
[Unit]
DefaultDependencies=no
Before=shutdown.target reboot.target halt.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/local/bin/taos-graceful-stop
TimeoutStopSec=60

[Install]
WantedBy=shutdown.target reboot.target halt.target
```

The `taos-graceful-stop` script:
```bash
#!/bin/bash
curl -fsS -X POST --max-time 25 "http://localhost:${TAOS_PORT:-6969}/api/system/prepare-shutdown" || true
```

As shipped it also dedupes the two calls a reboot makes (unit `ExecStop`
plus `taos-pre-shutdown.service`): a successful drain writes `prepare-shutdown.stamp`, and a second
invocation within 60 s is a no-op. The stamp lives in the unit's `RuntimeDirectory=taos`
(`/run/taos`, mode 0750; `$XDG_RUNTIME_DIR/taos` for the user unit, `$INSTALL_DIR/data` when there
is no systemd) and **never** falls back to a world-writable directory — a stamp any local user
could create is a silent, permanent kill switch for the drain. If no private directory qualifies,
the script drops the dedupe and drains twice rather than trust the stamp.

### Auto-update restart path

`AutoUpdateService._run_once` change:
- On successful auto-apply pull, write `~/.config/taos/pending-restart.json` with `{target_sha, pulled_at}`.
- If `auto_restart` pref is `true`: call `orchestrator.prepare("all", "update")`, then execute the restart (systemd / execv / docker).
- If `auto_restart` is `false` (default): emit a notification every **6 hours** until the user clicks "Restart now" in Settings. Dedup by not re-emitting while the latest one is unread.

### Resume on boot

Controller lifespan startup:
- If `pending-restart.json` exists: check current SHA vs `target_sha`. Emit "Update applied" (success) or "Restart happened but code didn't update" (failure).
- Start agent containers (they come up paused because config was persisted as paused).
- For each agent that has a `resume_note.json`: POST `/resume` to the agent. Agent acks when it has picked up context. Controller then clears `paused=False`.
- Clean up `pending-restart.json` and resume notes after successful resume.
- Emit "All agents resumed" when done.

### Settings UI

New toggles in Updates section:
- "Automatically restart after update" — **default OFF**.
- Helper text: "When off, we'll remind you every 6 hours until you restart."
- "Restart now" button, only shown when `pending-restart.json` exists.
- Live progress panel during orchestration: per-agent status chips (preparing → flushing → ready).

Agents app: pausing or stopping an agent shows the same progress panel inline with phase text:
`rejecting new jobs → flushing memory → writing resume note → ready`.

### Safety rails

- Each orchestration step emits a notification so a stuck flow is visible from the UI.
- `pending-restart.json` SHA check catches failed restarts (user on old code despite pull).
- Per-agent 300s timeout caps worst-case wait.
- "Cancel" on progress panel only interrupts *waiting*, not the agent's internal prep.
- Force-kill fallback if the container refuses to stop after the ack.

## What this prevents

- Lost tokens mid-LLM-response.
- Lost memories (synchronous flush before ack).
- Lost conversational context (snapshot written to resume note).
- Silent failure to apply an update (boot-time SHA check).
- User stuck after update because they don't know to restart.

## Out of scope

- SIGKILL paths (OOM, power loss, kernel panic) — future WAL-based recovery.
- Cluster worker graceful stop — separate orchestrator, own issue.
- Mid-inference pause/resume — would need backend-specific hooks.

## Implementation order

1. `restart_orchestrator.py` with the core primitive and stub agent-side call.
2. Agent-side `/prepare-for-shutdown` and `/resume` endpoints.
3. Wire controller SIGTERM → orchestrator.
4. Wire manual pause/stop → orchestrator.
5. Wire auto-update → orchestrator, with `auto_restart` pref and 6-hour reminder.
6. Boot-time resume logic and SHA check.
7. Systemd units + graceful-stop script, add to install script.
8. UI: progress panel, restart-now button, toggles.
9. End-to-end test on pi4-emulated LXC.
