# taOS Beach — sandbox provisioning

Status: design spec, not yet implemented. Phase 1 cut is defined at the end.

Beach lets an agent ask for a sandbox — an LXC container or a Docker container scoped to a
project — get it approved by the user, use it, share it with other agents on the same
project, and have it reclaimed when it is no longer needed. It is the third consumer of
`tinyagentos/containers/`, after the agent deployer and the userspace app deployer, and it
is the first one where the *requester is not the user*.

That last point is the whole design. Both existing consumers create containers on behalf of
a human who is already sitting in front of the UI clicking a button. Beach creates them on
behalf of an agent, so every question that the existing paths answer implicitly — who
authorised this, whose quota does it spend, when does it go away, who else may touch it —
has to become explicit state.

## What Beach is not

- **Not a new container backend.** `tinyagentos/containers/backend.py` already defines the
  `ContainerBackend` ABC (`create_container`, `exec_in_container`, `start`/`stop`/`restart`/
  `destroy_container`, `push_file`, `get_container_logs`, `rename_container`,
  `add_proxy_device`, `snapshot_create`/`_restore`/`_list`, `set_root_quota`, `spawn_pty`)
  with `lxc.py` (incus), `docker.py`, `native.py` and `apple_backend.py` behind it, selected
  by `detect_runtime()` / `configure_container_runtime()`. Beach calls that interface and
  adds nothing to it except the one gap named in §7.
- **Not a replacement for the agent deployer.** Agent containers (`taos-agent-*`, incus,
  built from the framework images in `app-catalog/`) keep their own path. A Beach sandbox is
  a blank environment with no LiteLLM wiring, no skills mount and no AGENTS.md.
- **Not a replacement for app containers.** `tinyagentos/userspace/container_deploy.py`
  keeps deploying `taos-app-*` from vetted manifests with its fixed 512m/1-CPU cap.
- **Not multi-tenant hosting.** Beach sandboxes are for work happening inside one taOS
  install, on hardware the user owns. Nothing here is a security boundary strong enough to
  run genuinely hostile code; see §11.

## 1. Object model

One row per sandbox, one row per grant. Both in `data/beach.db`, `BaseStore` idiom, schema
following `tinyagentos/agent_scope_requests_store.py`:

```sql
CREATE TABLE IF NOT EXISTS beach_sandboxes (
    id             TEXT PRIMARY KEY,          -- sbx-xxxxxx
    project_id     TEXT NOT NULL,             -- owning project; never NULL
    requested_by   TEXT NOT NULL,             -- agent canonical id (taos-x-YYYYMMDD-HHMMSS)
    runtime        TEXT NOT NULL,             -- 'lxc' | 'docker'
    image          TEXT NOT NULL,
    memory_mb      INTEGER NOT NULL,
    cpu_cores      INTEGER NOT NULL,
    disk_gib       INTEGER NOT NULL,
    ports          TEXT NOT NULL DEFAULT '[]',-- json: container ports to publish
    reason         TEXT NOT NULL DEFAULT '',  -- shown to the user at approval
    status         TEXT NOT NULL DEFAULT 'requested',
    container_name TEXT,                      -- taos-beach-<id>, set at provision
    node           TEXT,                      -- cluster worker name, NULL = this host
    host_ports     TEXT NOT NULL DEFAULT '{}',-- json: {container_port: host_port}
    dns_name       TEXT,                      -- <slug>.beach.<host>.local
    ttl_hours      INTEGER NOT NULL DEFAULT 24,
    expires_ts     TEXT,                      -- set when it enters running
    last_used_ts   TEXT,
    created_ts     TEXT NOT NULL,
    decided_ts     TEXT,
    decided_by     TEXT,                      -- username, or 'policy:<rule>' for auto-approve
    decision_id    TEXT,                      -- Decisions app row, for the audit trail
    destroyed_ts   TEXT,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS idx_beach_status  ON beach_sandboxes(status);
CREATE INDEX IF NOT EXISTS idx_beach_project ON beach_sandboxes(project_id, status);
CREATE INDEX IF NOT EXISTS idx_beach_expiry  ON beach_sandboxes(status, expires_ts);

CREATE TABLE IF NOT EXISTS beach_grants (
    sandbox_id   TEXT NOT NULL,
    canonical_id TEXT NOT NULL,               -- agent granted access
    access       TEXT NOT NULL DEFAULT 'use', -- 'use' | 'admin'
    granted_by   TEXT NOT NULL,
    created_ts   TEXT NOT NULL,
    PRIMARY KEY (sandbox_id, canonical_id)
);
```

`project_id` is not nullable on purpose. A sandbox with no project has no quota to spend, no
membership list to authorise against and nobody to inherit it when the requesting agent is
retired. If an agent has no project it cannot ask for a sandbox.

## 2. States

```
                      ┌──────────────── retry ─────────────┐
                      │                                    │
requested ─approve─> approved ─claim─> provisioning ─ok─> running ─stop─> stopped
    │                                       │               │  ▲            │
    │                                       └──error──> failed │            │
    └─refuse─> refused                                    ▲    │            │
                                                          └────┘            │
                              TTL / idle ────> expired <────────────────────┘
                                                  │
   any of {running, stopped, expired, failed} ─destroy─> destroying ──> destroyed
```

Read the edges, not the layout: `failed` is reachable only from `provisioning`, and `retry`
returns it to `approved` (never straight to `provisioning`, so the quota re-check in §4
always runs). `expired` is reachable from `running` and from `stopped`. `destroying` is
reachable from all four live-or-parked states, and `destroyed` only from `destroying`.

- `requested` — row exists, nothing has been created. The only state an agent can reach on
  its own.
- `approved` — a human (or a policy rule, §3) said yes. Nothing has been created yet:
  authorisation is recorded separately from the act, so a crash between the two leaves an
  approved request, not a mystery container.
- `provisioning` — a backend call is in flight. Single-flight: the transition into this
  state is `UPDATE ... SET status='provisioning' WHERE id=? AND status='approved'`, and a
  caller that updates zero rows lost the race and returns 409, exactly the guard
  `AgentScopeRequestsStore.decide()` uses for concurrent approvals.
- `running` / `stopped` — mirror the container. Reconciled against the backend, never
  assumed (§10).
- `expired` — TTL passed or idle reaping fired. Terminal for scheduling purposes but the row
  survives with its container destroyed, so the audit trail keeps the history.
- `failed` — the backend call failed; `error` carries the message. Recoverable by retry,
  which returns the row to `approved` without a second approval, because the user already
  said yes to this exact spec.
- `destroyed` — container gone, row kept.

Terminal states are `refused`, `destroyed`, `expired`, and `failed` only after the retry
budget is spent. Rows are never deleted; the Beach app filters on status.

## 3. Authorisation

Two new scopes in the closed vocabulary (`_ALLOWED_SCOPES`, `tinyagentos/routes/agent_registry.py`,
which also has to be mirrored into the agent route allowlist in `auth_middleware.py`):

- `sandbox_request` — may create a request, and may use sandboxes it has been granted.
- `sandbox_admin` — may approve, share, stop and destroy within its own project. Intended
  for a lead agent, not handed out by default.

Neither scope implies the other, and neither lets an agent touch a sandbox outside the
project its token is bound to.

The flow reuses what already exists rather than inventing a second consent surface:

1. Agent calls `POST /api/beach/sandboxes` (needs `sandbox_request`).
2. Beach writes the row `requested` and files a **Decisions** row
   (`tinyagentos/routes/decisions.py`) of type `approve_deny` at `blocking` priority, both
   drawn from that module's existing `DECISION_TYPES` / `PRIORITIES` vocabularies, carrying
   the spec and the reason. The user sees it in the Decisions inbox with the usual
   notification, and `decision_id` is stored on the sandbox row.
3. The user's answer flips the sandbox to `approved` or `refused`, and the Decisions
   answer-routing already posts the outcome back to the asking agent on the A2A bus, so an
   off-session agent learns its request landed without polling.
4. Provisioning happens only from `approved`, and only through the provisioner (§4).

**Auto-approve is a per-project policy, not a default.** A project may carry a rule like
"requests at or under 1 CPU / 1 GiB / 5 GiB disk with no published ports are auto-approved",
recorded on the sandbox row as `decided_by = 'policy:<rule-id>'` so an auto-approved sandbox
is never indistinguishable from a human-approved one in the audit trail. Ships disabled.

## 4. Provisioning

The provisioner is the only code that talks to `get_backend()`. It takes an `approved` row
and:

1. Claims it (`approved → provisioning`, single-flight as above).
2. Re-checks quota (§6) at claim time, not at request time. A request approved an hour ago
   must not be able to overrun a quota that filled up since.
3. Names the container `taos-beach-<id>`. The prefix matters: `list_containers(prefix=...)`
   is how every reconciliation pass finds its own containers, and the existing prefixes
   (`taos-agent-`, `taos-app-`) must not be shadowed.
4. Calls `create_container(name, image=..., memory_limit=..., cpu_limit=...,
   root_size_gib=...)`, then publishes ports (§7), then `set_root_quota` if the backend
   reported the disk quota as accounting-only.
5. On success: `running`, `expires_ts = now + ttl_hours`, `container_name`, `host_ports`,
   `dns_name` recorded. On failure: `failed` with `error`, and any partially created
   container destroyed before returning — a half-built sandbox is the one thing that must
   never be left behind, because nothing else will ever look for it.

## 5. HTTP surface

All under `/api/beach`, all requiring an authenticated session or an agent bearer whose
project matches the sandbox's `project_id`. Response shapes follow the project routes
(`tinyagentos/routes/projects.py`) and key off project **id**, not slug, the way the tasks
routes do.

| Method | Path | Who | Does |
| --- | --- | --- | --- |
| `POST` | `/api/beach/sandboxes` | agent w/ `sandbox_request`, or user | file a request → `requested` + Decisions row |
| `GET` | `/api/beach/sandboxes?project_id=` | project member | list (agent sees own + granted) |
| `GET` | `/api/beach/sandboxes/{id}` | owner, grantee, or `sandbox_admin` | detail incl. host ports, dns, expiry |
| `POST` | `/api/beach/sandboxes/{id}/approve` | user, or agent w/ `sandbox_admin` | `requested → approved` |
| `POST` | `/api/beach/sandboxes/{id}/refuse` | same | `requested → refused` |
| `POST` | `/api/beach/sandboxes/{id}/start` | grantee | `stopped → running`, extends expiry |
| `POST` | `/api/beach/sandboxes/{id}/stop` | grantee | `running → stopped` |
| `POST` | `/api/beach/sandboxes/{id}/destroy` | owner or `sandbox_admin` | → `destroying → destroyed` |
| `POST` | `/api/beach/sandboxes/{id}/exec` | grantee | one command, bounded timeout, returns rc + output |
| `GET` | `/api/beach/sandboxes/{id}/logs?lines=` | grantee | `get_container_logs` |
| `POST` | `/api/beach/sandboxes/{id}/grants` | owner or `sandbox_admin` | share with another agent on the project |
| `DELETE` | `/api/beach/sandboxes/{id}/grants/{canonical_id}` | same | revoke |
| `GET` | `/api/beach/quota?project_id=` | project member | used vs allowed, per project and per agent |

`exec` is deliberately one-shot request/response rather than a session. Interactive access
is the PTY path (`spawn_pty`) that the terminal app already uses, and it is out of the
Phase 1 cut.

## 6. Quotas

Two limits, both enforced at claim time in the provisioner and re-checked by the reconciler:

- **Per project**: max concurrent sandboxes, total memory, total CPU, total disk.
- **Per agent**: max concurrent sandboxes (default 2), so one looping agent cannot consume
  the whole project allowance.

Enforcement is a sum over `beach_sandboxes` rows in `provisioning`/`running`/`stopped`
status for that project, compared against the project's limits, refusing with 409 and a
message naming which limit was hit and what is currently holding it. Refusing without
naming the holder is what makes quota systems unusable.

Host capacity is a separate check and comes first: a host with 4 GiB of RAM runs core taOS
only, and Beach must refuse there with that reason rather than let the OOM killer answer.
The number comes from the same place `DiskQuotaMonitor`
(`tinyagentos/disk_quota.py`, constructed with config + container backend + notifications)
reads its thresholds, and Beach reuses that monitor for disk pressure rather than growing a
second one.

Disk enforcement is honest about the backend: `set_root_quota` is kernel-enforced on
btrfs/ZFS pools and **accounting-only** on dir-backed LXC pools and Docker overlay2 without
pquota. Beach records which of the two it got and shows "enforced" or "accounted" in the
app, because a quota the kernel is not enforcing is a report, not a limit.

## 7. Ports, binding and DNS

**The gap.** `ports` is a parameter of `DockerBackend.create_container` only. It is not on
the `ContainerBackend` ABC and not on `LXCBackend.create_container`; LXC publishes ports
through `add_proxy_device` instead. So a sandbox that publishes a port is not portable
across runtimes today. Beach must not paper over this with an `isinstance` check in the
provisioner. Fix it once, in the abstraction: add `ports: list[tuple[int, int]] | None` to
the ABC signature and implement it in `LXCBackend` in terms of `add_proxy_device`, then have
Docker keep its current behaviour. That is a small, separately reviewable change and it
should land **before** the Beach provisioner, not inside it.

Host ports come from `allocate_host_port` (`tinyagentos/installers/port_allocator.py`), the
same allocator the app deployer uses, so Beach cannot collide with an app, and apps cannot
collide with Beach. Every published port binds `127.0.0.1` only — same rule as
`container_deploy.py`. Reaching a sandbox from another machine goes through the existing
remote-access path, not by binding `0.0.0.0` here.

Core ports are never allocatable: `RESERVED_PORTS` in the allocator is the single place
that knows which those are, and Beach adds no second list.

DNS names are `<sandbox-slug>.beach.<host>.local`, published through the existing
`MdnsPublisher` (`tinyagentos/services/mdns_publisher.py`). mDNS stays on the current port
policy — publishing a name does not imply binding port 80.

## 8. Scheduling across nodes

Phase 1 is single-node: `node` is NULL and everything runs on the controller host.

The cluster registry already carries what multi-node scheduling needs —
`POST /api/cluster/workers`, `POST /api/cluster/heartbeat`, `GET /api/cluster/capabilities`
(`tinyagentos/routes/cluster.py`) — including per-worker hardware. Phase 2 picks a node by
filtering workers whose heartbeat is fresh and whose free memory exceeds the request, then
choosing the least loaded. Two rules to fix now, because retrofitting them is expensive:

- The sandbox row stores the node it landed on. A sandbox is not migratable in Phase 2;
  moving one means destroy and recreate.
- A worker that stops heartbeating does not free its sandboxes' quota automatically. Quota
  is released only when a container is confirmed gone, or a human forces the row to
  `destroyed`. Otherwise a flapping worker doubles a project's usage.

## 9. Harness-agnostic agent access

Beach is reachable identically from every framework taOS supports, because it is an HTTP
API plus one canonical ops skill — not a tool baked into one harness.

The canonical skill (`sandbox_request`, in the canonical ops-skills set) declares the tool
schema once:

```yaml
name: sandbox_request
description: Ask for a sandbox container scoped to a project, and use it once approved.
input_schema:
  project_id:  {type: string, required: true}
  runtime:     {type: string, enum: [lxc, docker], default: lxc}
  image:       {type: string, default: "images:debian/bookworm"}
  memory_mb:   {type: integer, default: 1024}
  cpu_cores:   {type: integer, default: 1}
  disk_gib:    {type: integer, default: 5}
  ports:       {type: array,   items: {type: integer}, default: []}   # CONTAINER ports
  ttl_hours:   {type: integer, default: 24}
  reason:      {type: string,  required: true}
```

An agent names **container** ports only — a flat list of integers. It never chooses a host
port: `allocate_host_port` does that at provision time and the result comes back in the
sandbox's `host_ports` map. That is why this schema is a list of integers while the backend
signature in §7 takes `(host, container)` pairs; the pairing is formed by the provisioner,
not requested by the caller.

Per-framework adapters carry no logic beyond shape translation:

| Framework | How it reaches Beach |
| --- | --- |
| taOS native agents | canonical skill, called as a tool |
| OpenClaw / ACP | ACP tool definition generated from the same schema |
| opencode | skill file in the agent's skills mount |
| MCP-speaking harnesses | one MCP tool per route, same schema |
| anything else | plain HTTP with the agent's bearer token |

Two constraints follow, and they are the reason this section exists rather than being
assumed: every field an agent can set must be expressible in JSON schema (no callbacks, no
framework objects), and no route may depend on a header only one harness sends. If Beach
ever needs something a plain HTTP client cannot do, the design is wrong.

## 10. Lifecycle, GC and reconciliation

- **TTL.** `expires_ts` is set on entry to `running` and extended on each `start` and each
  authenticated `exec`. On expiry the sandbox is destroyed and the row goes to `expired`.
  The requesting agent gets an A2A message an hour before, so long-running work can extend
  rather than lose state.
- **Idle reaping.** `last_used_ts` older than the idle window stops the container (not
  destroys it) and notifies. Stopped sandboxes still hold disk quota, and the app says so.
- **Reconciliation.** A periodic pass compares `list_containers(prefix="taos-beach-")`
  against the table in both directions. A container with no row is an orphan and is reported,
  never auto-destroyed — the one-sided version of this check is how you delete a user's work
  because a database file was restored from backup. A row claiming `running` with no
  container becomes `failed` with the discrepancy recorded.
- **Project deletion** destroys the project's sandboxes. Agent retirement does not: grants
  outlive the requester, and the project's admins inherit it.

## 11. Isolation, and what it is worth

Both runtimes give kernel-level isolation of processes, filesystem and network namespace,
which is the right boundary for "an agent's build should not scribble on the host". It is
not the right boundary for hostile code: neither unprivileged LXC nor Docker is a hypervisor,
and a kernel exploit crosses both. Beach's job is to state that plainly rather than imply
more.

What Beach does enforce:

- Sandboxes are unprivileged and get no host device passthrough in Phase 1 (that includes
  GPUs — GPU access goes through the arbiter, not through a sandbox holding a device node).
- No host bind mounts by default. A project may allow specific paths later; the default is
  none.
- Ports bound to loopback only.
- Distinct containers per sandbox; no shared network namespace between sandboxes, even
  within a project.
- Every state transition writes to the agent audit layer with the actor, the sandbox, the
  decision id and the outcome — the same record whether the actor was a human, a policy
  rule, or the reaper.

## 12. The Beach app

A desktop app listing sandboxes for the current project: status, runtime, node, resources,
published ports and DNS name, time to expiry, and who requested and who approved it. Pending
requests appear at the top with approve/refuse inline, the same decision the Decisions inbox
carries.

Per the live-surfaces rule the list refreshes itself — sandboxes change state without the
user acting, so a static list is wrong by construction. Controls: start, stop, destroy,
extend TTL, open terminal (Phase 2), manage grants. Quota shows used against allowed for the
project, with the "enforced" or "accounted" disk distinction from §6 visible rather than
buried.

## 13. Phase 1 cut

Single node, LXC and Docker, request → approve → create → use → destroy, with quota. What
ships:

1. `ports` added to the `ContainerBackend` ABC and implemented for LXC via
   `add_proxy_device` (§7) — separate PR, lands first.
2. `BeachStore` with both tables, the single-flight claim, and the quota sum.
3. Provisioner over `get_backend()`, with the failure path that destroys partial containers.
4. Routes: create, list, get, approve, refuse, start, stop, destroy, exec, logs, quota,
   and both grant routes. Sharing is not an extra — "request, share, spin up" is the ask —
   so grants ship in Phase 1 with everything else in §5.
5. `sandbox_request` and `sandbox_admin` in `_ALLOWED_SCOPES` plus the agent route allowlist.
6. Decisions integration for approval, and audit records on every transition.
7. TTL expiry and the reconciliation pass.
8. Beach app: list, pending approvals, start/stop/destroy, quota display.
9. Canonical `sandbox_request` skill so a non-taOS-native agent can use it on day one.

Acceptance, all as tests: an agent without `sandbox_request` is refused on every route; an
agent with it can file a request but cannot approve its own; approval is required before any
container exists; two concurrent provisions of one row produce exactly one container and one
409; a request exceeding project or agent quota is refused naming the limit and the holder;
a failed create leaves no container behind; TTL expiry destroys the container and moves the
row to `expired`; an orphan container is reported and not destroyed; a sandbox in project A
is invisible and unreachable to an agent bound to project B.

Explicitly not in Phase 1: multi-node scheduling, PTY/terminal, snapshots, host bind mounts,
device passthrough, migration, auto-approve policy rules.

## 14. Decisions still open (need Jay)

1. **Default quotas.** Proposed: 2 sandboxes per agent, 4 per project, 4 GiB memory and 20
   GiB disk per project on a 16 GiB host, scaled from host RAM. Needs a number, not a
   formula, before Phase 1 ships.
2. **Default runtime.** LXC matches the agent path and gives the better isolation story;
   Docker is what most published images target. Proposed: LXC default, Docker when the
   request names a Docker image.
3. **Auto-approve.** Ships disabled and off by default. Confirm it should exist at all —
   the alternative is that every sandbox costs a human tap forever, which is the safer
   default and the more annoying one.
4. **Sandbox count vs the low-RAM rule.** On a 4 GiB host Beach refuses entirely. Confirm
   that is the behaviour rather than a smaller allowance.

## Related

- `docs/design/container-runtime-abstraction.md` — the backend interface Beach builds on
- `docs/design/lxc-docker-coexistence.md` — why both runtimes can be present at once
- `docs/agent-coordination.md` — where the `/api/beach` routes get documented when they land
