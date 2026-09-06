# TinyAgentOS Complete & Polish Design

**Status:** Implemented — the five workstreams listed below have landed on `master`. Retained for historical context.

Five independent workstreams that fill gaps identified by a full codebase audit. No new architecture — everything extends existing patterns (FastAPI routes, htmx partials, Pico CSS, SQLite via BaseStore, pytest).

## A) Dashboard & Monitoring Polish

### Cluster KPIs on Dashboard

Add a cluster summary card to `dashboard.html` showing:
- Total workers (online/offline)
- Aggregate cluster RAM and VRAM
- Worker health badges (green = heartbeat < 30s, yellow < 120s, red = stale)

Data source: `app.state.cluster_manager.workers` dict — already populated by worker heartbeats.

New API endpoint: `GET /api/dashboard/cluster-summary` returns `{workers: int, online: int, total_ram_gb: float, total_vram_gb: float}`.

### Centralized Event System

Current state: notifications are ad-hoc — each route manually calls `await notif_store.add(...)`. No guarantees that events like backend-down or worker-join emit notifications.

Add `emit_event(event_type, title, message, level, source)` to `NotificationStore`. This is a thin wrapper around `add()` that:
1. Checks a muted-events set (loaded from a `notification_prefs` table)
2. If not muted, stores the notification and fires webhooks

Wire up events in:
- `cluster/manager.py` — worker join/leave
- `backend_adapters.py` or health monitor — backend up/down state changes
- `training.py` — job complete/failed
- `routes/store.py` — app install complete/failed

### Notification Preferences

Add a `notification_prefs` table to `NotificationStore`:
```sql
CREATE TABLE IF NOT EXISTS notification_prefs (
    event_type TEXT PRIMARY KEY,
    muted INTEGER NOT NULL DEFAULT 0
);
```

Event types: `worker.join`, `worker.leave`, `backend.up`, `backend.down`, `training.complete`, `training.failed`, `app.installed`, `app.failed`.

Settings page gets a new htmx partial showing toggles for each event type.

### Notification Cleanup Job

On startup in `app.py` lifespan, register a daily cleanup task via `scheduler.add_task()` if one doesn't already exist. Calls `notif_store.cleanup(max_age_days=30)`.

## B) Deployment Wizard — Background Deploy

### Current Problem

`POST /api/agents/deploy` calls `await deploy_agent(req)` synchronously. This blocks for 2-5 minutes (container creation, apt-get, npm install, framework install). The HTTP request times out or the user stares at a spinner.

### Solution

1. **Background task dict** — `app.state.deploy_tasks: dict[str, dict]` tracks active deployments. Each entry: `{status: "deploying"|"success"|"failed", step: str, error: str|None, result: dict|None}`.

2. **Deploy endpoint** — instead of awaiting, kick off `asyncio.create_task(background_deploy(...))` and return immediately with `{"status": "deploying", "name": body.name}`. The background coroutine updates `deploy_tasks[name]` as it progresses.

3. **Status endpoint** — `GET /api/agents/{name}/deploy-status` returns the current deploy task state. Returns 404 if no deploy in progress.

4. **Agent status field** — add `"status": "deploying"` to the agent config entry when deploy starts. Updated to `"running"` on success, `"failed"` on error.

5. **UI polling** — agents list page adds `hx-trigger="every 5s"` on agents with `status == "deploying"`. Shows a "Deploying..." badge with the current step name. On completion, badge updates to "Running" (green) or "Failed" (red with error tooltip).

6. **Rollback** — deployer already destroys container on failure. Background task additionally removes the agent config entry and emits a notification.

### Deploy Request Changes

Add `framework` and `model` to the wizard's step 5 POST. The existing `DeployAgentRequest` model already has these fields.

## C) Missing UI Pages

### Shared Folders Page (`/shared-folders`)

New template `shared_folders.html` + route handler in `routes/shared_folders.py`.

Layout:
- Table of folders: name, owner, file count, access list (agent badges)
- "New Folder" button → modal with name input + agent multi-select
- Per-folder row: expand to see files, manage access, delete

All CRUD already exists in `SharedFolderManager`. The page just renders it.

### Channel Hub Page (`/channel-hub`)

New template `channel_hub.html` + extend `routes/channel_hub.py`.

Layout:
- Table of agents with their connected channels (Telegram icon, Discord icon, etc.)
- Per-agent row: channel status badges (connected/disconnected), adapter type
- "Connect Channel" button per agent → dropdown of channel types → config form
- Uses existing `ChannelStore.list_all()` and `channel_hub_connectors` state

### Model Conversion Page (`/conversions`)

New template `conversions.html` + extend `routes/conversion.py`.

Layout:
- Table of conversion jobs: source model, target format, status, progress
- "New Conversion" button → form with model select + target format select
- Status badges: queued, converting, complete, failed
- Uses existing `ConversionManager` store

## D) Test Coverage

Add test files for 10 untested route modules. Each file uses existing `conftest.py` fixtures (async client with mock app state).

Target tests per module:
| Route Module | Test Count | Key Scenarios |
|---|---|---|
| auth | 6 | login, logout, session validation, bad password, expired session, exempt paths |
| channels | 6 | add channel, list, remove, toggle, list for agent, list all |
| channel_hub | 5 | connect, disconnect, status, webhook incoming, list connectors |
| conversion | 5 | create job, list, get, update status, delete |
| import_data | 4 | upload file, list imports, invalid file type, large file |
| notifications | 6 | list, unread count, mark read, mark all read, cleanup, preferences |
| settings | 6 | get settings, update setting, backup, restore, test backend, webhooks |
| shared_folders | 5 | create folder, list, grant access, revoke access, delete |
| training | 5 | create job, list, get, update status, delete |
| workspace | 5 | workspace page, messages, files, send message, contacts |

Total: ~53 new tests → bringing suite to ~750.

## E) Settings & Admin Enhancements

### LLM Proxy Status Card

Add a card to `settings.html` showing:
- Proxy status: Running (green) / Stopped (red) / Not Installed (grey)
- Port number
- Configured backend count
- Active virtual key count (per-agent keys)
- "Restart Proxy" button (calls `llm_proxy.restart()`)

Data from `app.state.llm_proxy` — all methods already exist.

### ChannelStore CRUD Gaps

Add to `channels.py`:
- `get(channel_id: int) -> dict | None` — fetch single channel by ID
- `update(channel_id: int, config: dict) -> None` — update channel config JSON

### AgentMessageStore Gaps

Add to `agent_messages.py`:
- `delete(message_id: int) -> bool` — delete a single message
- `search(query: str, agent_name: str | None, limit: int) -> list[dict]` — full-text search across message content

### Import Data Embedding

Current state: `routes/import_data.py` line 69 stubs out embedding — files are validated but never embedded.

Fix: after file upload, POST the file content to the agent's QMD serve URL (`agent["qmd_url"]`) at `/api/embed` endpoint. The QMD serve instance handles chunking and embedding. Fall back gracefully if QMD is unreachable (store file, mark as "pending embedding").

## Non-Goals

- No new agent frameworks or connectors in this pass
- No changes to LXC container architecture
- No new hardware detection
- No mobile app work
- No Playwright E2E tests (separate issue #8)
