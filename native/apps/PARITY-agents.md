# Parity: native Agents

Source of truth: the web app and routes at `origin/dev` (88fe9372f). File:line references are into that tree.
Web: `desktop/src/apps/AgentsApp.tsx`, `desktop/src/apps/agents/*`, `AgentSkillsPanel.tsx`,
`AgentMessagesPanel.tsx`, `AgentBrowsersApp.tsx`, the settings tabs in `desktop/src/components/agent-settings/`.
Server: `tinyagentos/routes/agents.py` (`A`), `agent_registry.py` (`R`), `agent_auth_requests.py` (`Q`),
`taos_agent.py` (`T`), `shortcuts.py`, `trace.py`, `librarian.py`, `framework.py`, `disk_quota.py`, `skills.py`,
`workspace.py`. Code: `native/apps/agents/`, client `native/taos-client/src/agents.rs`.

Status: **first cut** (this commit), **later** (planned, order below), **n/a** (not for a native desktop app, or
no web feature behind it). "Who" is what the handler checks today: `owner/admin` is
`require_agent_owner_or_admin` (`auth_context.py:129`: an admin, or the registry owner of the agent, else 403);
`admin` is `require_admin`; `any session` means the handler adds no per-agent check of its own.
The native app never assumes a member may act: every action can come back 403 (or 404 for existence-hiding
routes) and the app shows the controller's sentence next to the control, then refetches.

## What the web app really has

The detail view has the tabs Logs, Traces, Persona, Memory, Framework, Skills, Messages, Shortcuts
(`AgentDetailPanel.tsx:30,121-156`). It has **no** Overview, Model, Permissions, Budget or Channels tab.
Model change lives in the Framework tab (`FrameworkTab.tsx:62`), grants in the Registry panel
(`RegistryPanel.tsx:344-361`), budget and wake-budget have routes but no web UI (grep of `desktop/src` finds none).
The native Overview is therefore an addition, built only from fields the routes return. Uptime is returned by no
route, so the native app shows "not reported" and never computes one.

## List, identity, status

| Web feature | Route (origin/dev) | Who | Status | Notes |
|---|---|---|---|---|
| Agent list, name, framework, model, host, vectors | `GET /api/agents` `A:261` | any session; rows filtered: admin and local token see all, a member sees only agents the registry attributes to them (`_visible_to_caller` `A:213`) | first cut | row fields `name display_name host color emoji status vectors framework model paused ...`; `llm_key` is stripped (`A:187`) |
| Live status overlay (Running, Frozen, Stopped, Error) | `GET /api/agents/containers` `A:281` | same filter as the list | first cut | web `applyLiveState` (`AgentsApp.tsx:47-62`): Frozen reads as running+paused; a failure of this call must not fail the list (`AgentsApp.tsx:34-44`); the store does the same |
| Paused chip (flag or Frozen), stopped beats a stale paused flag | n/a (derived) | | first cut | `AgentRow.tsx:228-232`; status always has a text label |
| Status text: Running, Stopped, Error, Deploying, Paused | n/a (derived) | | first cut | `AgentRow.tsx:19-40` |
| Built-in taOS agent row (no lifecycle buttons, "protected") | `GET /api/taos-agent/config` `T:356` | any session | first cut | pinned first row, model from config, no start/stop/delete; detail panel is later |
| taOS agent detail (settings, model, permitted models, persona, activity, shell profile) | `T:334-534`, `GET /api/agents/taos-agent/trace` | admin for writes (`T:341`) | later | `TaosAgentDetailPanel.tsx` |
| Owner shown in the admin view | `GET /api/agents/registry` `R:613` joined on `registry_canonical_id` to `user_id` | admin sees all rows; a member sees their own | first cut (id only) | the row has no owner field; a user display name needs a users route, so the id is shown |
| Disk quota state and warning card | `GET /api/agents/{name}/disk` (`disk_quota.py:42`) | any session | later | `AgentsApp.tsx:239-262`, 80/90 percent states |
| Expand quota +10 GB | `POST /api/agents/{name}/quota` (`disk_quota.py:52`) | any session | later | 409 on dir backend has its own sentence (`AgentsApp.tsx:312`) |
| Framework "update available" marker | `GET /api/agents/{slug}/framework` (`framework.py:31`), `GET /api/frameworks/latest` | any session | later | |
| Framework update | `POST /api/agents/{slug}/framework/update` (`framework.py:60`) | any session | later | |
| Device the agent runs on: name, kind, reachable or offline; group and filter by device | config row `remote` = worker name (`agents.py:1114`, absent on the controller's own device); kind and status from `GET /api/cluster/workers` (`kind`, `status`) | any session for the list (a non-admin gets a reduced worker record) | first cut | name = worker name; no display name or icon field exists server side, so the icon is chosen from `kind` (`phone`, `worker`, controller). If the cluster list is refused the device shows as "unknown", never as failed. Missing server field: a per-agent device display name and a device kind that is not the worker `kind` string |
| Search and filter | n/a | | first cut | beyond web: text search, status filter, keyboard |
| Live updates | polling; no agent event is published (`/api/os/events` has no agent kind that is emitted) | | first cut | visible-only poll plus refetch on every stream reconnect and after every action |
| Refresh on focus | n/a | | first cut | window activation refetches |
| Skeleton, empty, error, 403 states | n/a | | first cut | |

## Lifecycle

| Action | Route | Who | Status | Notes |
|---|---|---|---|---|
| Start | `POST /api/agents/{name}/start` `A:1381` | owner/admin | first cut | 500 `{"error"}` when the container will not start |
| Stop (confirm) | `POST .../stop` `A:1461` | owner/admin | first cut | 200 with `stop_result.success == false` and no `force_killed` is a failure (`AgentsApp.tsx:204-209`) |
| Restart (confirm) | `POST .../restart` `A:1541` | owner/admin | first cut | |
| Pause | `POST .../pause` `A:1405` | owner/admin | first cut | 404 no container, 409 not running, 500 freeze failed |
| Resume | `POST .../resume` `A:1783` | owner/admin | first cut | unfreezes a Frozen container, clears the flag |
| In-flight lock per agent, refetch after | n/a | | first cut | `AgentsApp.tsx:162-215` |
| Archive ("Delete" in the web row, restorable) | `DELETE /api/agents/{name}` `A:580`; `DELETE .../destroy` `A:1714` is the same | owner/admin | later | confirm text `AgentsApp.tsx:239` |
| Archived list | `GET /api/agents/archived` `A:320` | any session; filtered by registry owner | later | `ArchivedAgents.tsx` |
| Restore | `POST /api/agents/archived/{id}/restore` `A:1727` | owner/admin of the archived entry | later | |
| Purge (type-to-confirm in native) | `DELETE /api/agents/archived/{id}` `A:1734` | owner/admin of the archived entry | later | |
| Bulk start, stop, restart | `POST /api/agents/bulk/*` `A:1266-1372` | admin | later | no web UI uses them; low priority |
| Reconcile orphan channels, containers | `A:1741`, `A:1757` | admin | n/a | operator routes, no web UI |

## Detail tabs

| Tab (web) | Routes | Who | Status | Notes |
|---|---|---|---|---|
| Overview (native only) | list row, `containers` row (ip, memory_mb, cpu_cores, status), `GET /api/agents/{name}` `A:457`, `GET .../trace?limit=` | `_require_agent_access`: owner, admin, or the agent itself | first cut | status, model, framework, host, vectors, container, last activity from trace `created_at`; uptime "not reported" |
| Logs, 10 s refresh | `GET /api/agents/{name}/logs?lines=100` `A:1555` | owner/admin/agent | later (live tail) | `AgentDetailPanel.tsx:62-85` |
| Traces and OTel spans | `GET .../trace` `trace.py:68`, `GET .../otel-spans` `trace.py:103` | any session (audit rows filtered to the caller) | later | |
| Persona (soul, agent md) | `PATCH /api/agents/{slug}/persona` `A:1820` | owner/admin | later | |
| Memory (plugin, mode, librarian) | `PATCH .../memory` `A:1843`, `GET/PATCH .../librarian` `librarian.py:26,34`, `GET /api/memory/stats` | owner/admin for the PATCH | later | |
| Framework and model | `POST .../model` `A:1878`, `GET/PUT .../permitted-models` `A:2006,2021`, `GET /api/providers/models` | owner/admin for writes | later (model, permitted models) | 400 not chat capable, 409 not reachable; both sentences shown |
| Skills | `GET /api/skills`, `GET/POST/DELETE /api/agents/{id}/skills` `skills.py:12,32,38,51` | any session | later | |
| Messages | `GET/POST /api/agents/{name}/messages` `workspace.py:59,66` | read: owner/admin/agent | n/a in this app | the native Messages app owns it; here a "Message" button launches `taos-messages --agent <name>` |
| Shortcuts (terminal, TUI, dashboard) | `GET /api/agents/{id}/shortcuts` `shortcuts.py:42`, `POST .../launch` `shortcuts.py:71` | any session | later | launches need the native Terminal and Browser apps |
| Budget (cap, spend, reset) | `GET/PUT /api/agents/{name}/budget` `A:2120,2135`, `POST .../budget/reset` `A:2155`, `GET .../wake-budget` `A:2168` | read owner/admin/agent; write owner/admin | later | beyond web (no web UI) |
| Channels | none: no agent channel route or web panel | | n/a | no web feature to port; the agent's DM channel belongs to Messages |
| Migration banner, dismiss | `POST .../dismiss-migration-banner` `A:1861` | owner/admin | later | |
| Export | `GET .../export` `A:1563` | owner/admin/agent | later | needs a save dialog |

## Access, grants, registry identities

| Web feature | Route | Who | Status | Notes |
|---|---|---|---|---|
| Requests tab (scope requests, count badge) | `GET /api/agents/scope-requests?status=` `Q:2765` | admin or agent owner | later | the client already has `decisions().scope_requests()`; approve and deny are `notifications` ops; not duplicated in `agents.rs` |
| Access requests (new external agents) | `GET /api/agents/auth-requests` `Q:2478`, approve `Q:780`, deny `Q:2439` | admin | later | client: `decisions().access_requests()`; the Decisions app already answers them |
| Registry list, detail, edit, suspend, reactivate, remove, rotate tokens | `R:613-827` | list: admin all, member own; writes see route | later | `RegistryPanel.tsx` |
| Grants per agent, assign to project, revoke | `GET /api/agents/registry/grants` `R:566`, `POST /api/projects/{id}/members/assign-agent` `Q:2145`, `revoke-agent` `Q:2210` | grants read: admin | later | `AgentGrantsPanel.tsx`, `AssignAgentToProjectDialog.tsx` |
| Org (reports to) | `GET /api/agents/org` `R:972`, `PUT /api/agents/{id}/org` `R:986` | see route | later | |
| Invites | `POST /api/agents/invites` (`RegistryPanel.tsx:952`) | see route | later | |
| Base images panel | `BaseImagesPanel.tsx` | admin | later | |

## Create, import, deploy

| Web feature | Route | Who | Status | Notes |
|---|---|---|---|---|
| Deploy wizard (framework, model, persona, memory, resources, worker pin, KV quant, save persona) | `POST /api/agents/deploy` `A:690` (plus `/api/frameworks`, `/api/hardware`, `/api/models`, `/api/providers`, `/api/cluster/*`, `/api/taosmd/*`) | admin | later (native wizard) | first cut: empty state says so and opens the web wizard (`xdg-open <controller>/`) when a controller URL is known |
| Deploy progress | `GET /api/agents/{name}/deploy-status` `A:445` | owner/admin/agent | later | |
| Import wizard | `POST /api/agents/import` `A:1617` | admin | later | |
| Create bare agent | `POST /api/agents` `A:467` | admin | n/a | no web UI calls it |
| Agent browsers app | `AgentBrowsersApp.tsx` | | n/a | a separate app (browser profiles), not the Agents app |

## First-cut counts

First cut: 15 rows. Later: 33. Not applicable: 6. The table order above is the order of the later work:
model and permissions, logs (live tail), skills, persona and memory, budget, archive and restore and purge,
access requests tab, grants, then the deploy wizard.
