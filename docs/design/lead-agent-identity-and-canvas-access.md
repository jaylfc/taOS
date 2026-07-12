# Lead-agent identities and external-agent canvas access (founding design)

Status: DRAFT for Jay review. Plan only, no implementation.
Tracking: taOS project board task #98.
Builds on: the consent loop (`docs/design/external-agent-onboarding.md`, #744),
the `project_tasks` scope and its anchored middleware allowlist (#1774), the
invite flow draft (`docs/design/external-agent-project-invite.md`, on
`origin/docs/external-agent-invite`), and the nested-elements design
(`docs/design/projects-nested-elements.md`).

All code citations below were verified against `dev` at `9d006f47`.

## Why this exists

Jay's org model for the taOS project is a three-lead structure. @taOS-dev,
@taOSmd-dev and @taOS-website-dev are the LEADS on the three elements of the
taOS project (the OS, the memory system, the website), while grok and kilo are
tools, subagents the leads pick up and use. The assumption behind day-to-day
operation was that every one of those five agents had a minted taOS identity,
joining projects and the a2a bus the same way grok and kilo do, and that the
canvas checkbox on the project Members screen actually controlled who can edit
the canvas.

Reality, found in code and on the box:

1. **Only jay's admin account exists.** The two other leads have been logging
   in AS JAY, with copied user credentials. This came to light the hard way:
   @taOS-website-dev broke on a stale password (the copy it held no longer
   matched after a rotation). The recovery path that exists for humans,
   `admin_reset_password` at `tinyagentos/routes/auth.py:728`, mints a new
   invite code for a USER, which is exactly the wrong shape for an agent.
2. **The Members-screen canvas checkbox is decorative over HTTP.** The PATCH
   endpoint writes `project_members.can_edit_canvas`
   (`tinyagentos/routes/project_canvas.py:146`), the column exists
   (`tinyagentos/projects/project_store.py:34`), the store even contains an
   enforcement helper keyed on it
   (`tinyagentos/projects/canvas/store.py:152`), but no HTTP code path ever
   reaches that helper as an agent, so nothing the checkbox controls can
   actually happen or be denied.
3. **The auth middleware does not accept agent tokens on canvas routes at
   all.** The registry-JWT pass-through is a closed allowlist
   (`tinyagentos/auth_middleware.py:36` and the anchored task matcher at
   `tinyagentos/auth_middleware.py:62`), and canvas paths are not in it. An
   agent presenting its own identity on a canvas route gets a 401 before any
   handler runs. Logging in as jay was not laziness, it was the only path
   that worked.

Interim applied 2026-07-12: @taOS-website-dev was given a non-admin USER
account via the invite-code flow (`/auth/complete`,
`tinyagentos/routes/auth.py:589`; pending-invite login at
`tinyagentos/routes/auth.py:408`). That unblocks the website lead today, but
it is a human-account workaround for an agent-identity problem. **This design
retires that interim.** End state: jay remains the only human account, and
every lead is a registry-minted agent identity.

## Current state, verified in code

### The agent-token surface (what leads SHOULD be using)

- The middleware pass-through is deliberately closed: exact paths in
  `_AGENT_TOKEN_PATHS` (`tinyagentos/auth_middleware.py:36`, registry feeds +
  a2a bus proxy) plus the anchored `(method, regex)` task matcher
  `_AGENT_TASK_ROUTES` / `_is_agent_task_path`
  (`tinyagentos/auth_middleware.py:49-65`). Anything not listed never sees a
  registry JWT (`tinyagentos/auth_middleware.py:255-262`). No skeleton key.
- Route-level verification is `check_agent_scope_for_project`
  (`tinyagentos/agent_token_auth.py:151-190`): EdDSA signature against the
  registry pubkey, agent `active` in the registry, an unexpired grant for the
  required scope, the token's `project_id` claim matching the requested
  project, AND the grant row itself bound to that project (defense in depth).
  Mismatches collapse to the existence-hiding 404 via
  `PROJECT_SCOPE_MISMATCH_DETAIL` (`tinyagentos/agent_token_auth.py:78`).
- The task routes resolve either a session owner/admin or a project-bound
  agent through `_authorize_task_actor`
  (`tinyagentos/routes/projects.py:426-469`), and bind lifecycle actors to
  the token identity via `_resolve_actor`
  (`tinyagentos/routes/projects.py:472-488`). This is the pattern canvas
  routes must copy.
- The consent flow mints identities: closed scope vocabulary `VALID_SCOPES`
  (`tinyagentos/routes/agent_auth_requests.py:44-57`), unknown scopes
  rejected at request time
  (`tinyagentos/routes/agent_auth_requests.py:157-162`), granted scopes must
  be a subset of requested (narrow, never widen,
  `tinyagentos/routes/agent_auth_requests.py:289-299`), `project_tasks`
  requires an admin-picked project
  (`tinyagentos/routes/agent_auth_requests.py:308-312`), the minted JWT
  carries `project_id` as a top-level claim
  (`tinyagentos/routes/agent_auth_requests.py:349-355`), and approval adds
  the agent as a project member and syncs the project a2a channel
  (`tinyagentos/routes/agent_auth_requests.py:369-393`). Canonical ids come
  from `mint_canonical_id`, `{slug}-{YYYYMMDD}-{HHMMSS}`
  (`tinyagentos/agent_registry_store.py:301-305`).
- A parallel internal-mint path exists for exactly the lead handles:
  `_INTERNAL_AGENTS` lists @taOS-dev, @taOS-website-dev, @taOSmd-dev, @Hermes
  (`tinyagentos/routes/agent_registry.py:170-175`), seeded idempotently by
  handle with a2a scopes only
  (`tinyagentos/routes/agent_registry.py:169, 373-419`), and
  `POST /api/agents/registry/mint-internal` accepts arbitrary allowed scopes
  and a `project_id` (`tinyagentos/routes/agent_registry.py:346-370`).
  Crucially, neither internal path adds a `project_members` row, so the
  canvas checkbox has no row to govern for a seeded lead.
- a2a send requires a registry HANDLE: an agent-token send derives `from`
  from the record's own handle and 403s with "agent has no bus handle" when
  it is empty (`tinyagentos/routes/a2a_bus.py:171-175`). The consent flow
  registers with `handle=""`
  (`tinyagentos/routes/agent_auth_requests.py:327-333`), which matters for
  the migration below. The registry store can update a handle
  (`tinyagentos/agent_registry_store.py:573-605`).

### The canvas surface (what is actually broken)

- CRUD handlers author every write as a session user:
  `author_kind="user", author_id=_user_id(request)` on create, update and
  delete (`tinyagentos/routes/project_canvas.py:68, 94, 109`).
- `_user_id` is itself broken: it reads `request.state.user`
  (`tinyagentos/routes/project_canvas.py:24-28`), but the middleware only
  ever sets `request.state.user_id`
  (`tinyagentos/auth_middleware.py:281`). Nothing in the tree assigns
  `request.state.user`, so every element ever written over HTTP is attributed
  to the literal string `"system"`. Provenance on the canvas is fiction
  today, even for humans.
- The store DOES enforce `can_edit_canvas`, but only for `author_kind="agent"`
  and only on update/delete (`tinyagentos/projects/canvas/store.py:152-168`,
  called at `:179` and `:217`). `add_element` never checks it
  (`tinyagentos/projects/canvas/store.py:95-139`), so even the in-process
  agent tool path (`tinyagentos/projects/canvas/mcp_tools.py:38-51` and
  siblings, which correctly pass `author_kind="agent"`) can CREATE elements
  with the checkbox off; it is only blocked from editing them afterwards.
- User sessions face no project check at all: any authenticated user can
  list, create, edit and delete on ANY project's canvas, and can flip any
  project's permission checkbox (`tinyagentos/routes/project_canvas.py:146`
  has no owner/admin gate). Compare the task routes, where `_get_owned_project`
  applies existence-hiding 404 to non-owners
  (`tinyagentos/routes/projects.py:405-414`). With jay as the only account
  this was moot; the interim website-dev USER account made it live.
- No payload size cap and no rate limit exist on canvas writes:
  `CreateElementIn.payload` is an unbounded dict
  (`tinyagentos/routes/project_canvas.py:42`) serialized whole into SQLite
  (`tinyagentos/projects/canvas/store.py:132, 188`). Verified: no
  content-length or serialized-size check anywhere on the canvas path.
- The SSE stream (`tinyagentos/routes/project_canvas.py:172-199`) and the two
  snapshot endpoints (`:120`, `:137`) are session-gated by the middleware and
  nothing else; agent tokens cannot reach them.
- The store already records `author_kind` and `author_id` on every element
  (`tinyagentos/projects/canvas/store.py:19-20`), and the agent-allowed kind
  set already excludes `user_shape`
  (`tinyagentos/projects/canvas/store.py:46-49, 106-107`). The data model for
  honest attribution needs no schema change.

## The org model: leads and tools

This section is the durable statement of the structure the rest of the design
serves.

**Leads** (@taOS-dev, @taOSmd-dev, @taOS-website-dev):

- Long-lived, registry-minted identities, one per lead, never per-session.
  Stable identity is the same decision the onboarding design locked for
  coding agents (`docs/design/external-agent-onboarding.md`, "identity is
  STABLE per (tool, project)").
- Members of the taOS project, with `can_edit_canvas` enabled by the human.
- May claim, release, close, reopen and comment on tasks (the `project_tasks`
  scope, exactly as shipped), coordinate on the project a2a channel
  (`a2a_send` + `a2a_receive`), and read and write the project canvas
  (the two new scopes below).
- A lead is the natural assignee of a project ELEMENT. The nested-elements
  design already provides the slot: `element.assignee_id` names one project
  member, default-routes new element-tagged tasks to them at create time, and
  pins their ownership on the element card ("Whole-element assignment" and
  slice 5 in `docs/design/projects-nested-elements.md`). When the taOS trio
  is grouped into one project, each lead becomes the assignee of its element.
  Nothing in this document changes that design; this document supplies the
  identities that assignment expects to point at.

**Tools** (grok, kilo, and future executors):

- Task-scoped executors, invoked by leads or the orchestrator, not
  self-directed members of the org.
- Board-only access: `project_tasks` for their bound project, nothing else by
  default. No canvas write, ever, in this design (canvas read is an open
  question below). Their identities remain consent-minted exactly as today.

**Humans**: jay, the only human account. The multi-user machinery in
`routes/auth.py` stays for actual humans; it stops being an agent workaround.

## Design

### D1. Scope vocabulary: `canvas_read` and `canvas_write`

Extend the closed vocabulary in BOTH places it is defined:

- `VALID_SCOPES` in `tinyagentos/routes/agent_auth_requests.py:44-57`
  (consent requests), and
- `_ALLOWED_SCOPES` in `tinyagentos/routes/agent_registry.py:89-96`
  (internal mint), which must stay in lockstep.

Semantics:

- `canvas_read`: list elements, receive the SSE stream, fetch snapshots, for
  the token's bound project only.
- `canvas_write`: create, update, delete canvas elements on the bound
  project, subject to the membership flag in D3. Write does not imply read;
  leads request both.

Both scopes are project-bound the same way `project_tasks` is: granting
either without a picked project is rejected at approve time (extend the
`project_tasks` guard at `tinyagentos/routes/agent_auth_requests.py:308-312`
to a set `{"project_tasks", "canvas_read", "canvas_write"}`). The
narrow-not-widen rule and the unknown-scope rejection apply unchanged because
they operate on the vocabulary set, not on individual scopes.

### D2. Middleware: canvas routes join the closed allowlist

Add a second anchored matcher beside `_AGENT_TASK_ROUTES`, same style, same
contract (`tinyagentos/auth_middleware.py:48-65`):

```python
_AGENT_CANVAS_ROUTES = (
    ("GET",    re.compile(rf"^/api/projects/{_SEG}/canvas/elements$")),
    ("POST",   re.compile(rf"^/api/projects/{_SEG}/canvas/elements$")),
    ("PATCH",  re.compile(rf"^/api/projects/{_SEG}/canvas/elements/{_SEG}$")),
    ("DELETE", re.compile(rf"^/api/projects/{_SEG}/canvas/elements/{_SEG}$")),
    ("GET",    re.compile(rf"^/api/projects/{_SEG}/canvas/stream$")),
    ("GET",    re.compile(rf"^/api/projects/{_SEG}/canvas/snapshot\.png$")),
    ("GET",    re.compile(rf"^/api/projects/{_SEG}/canvas/snapshot\.tldr$")),
)
```

with `_is_agent_canvas_path(method, path)` mirroring `_is_agent_task_path`,
and the dispatch branch at `tinyagentos/auth_middleware.py:255-262` extended
to `or _is_agent_canvas_path(...)`.

Deliberately NOT in the allowlist:

- `PATCH /api/projects/{pid}/canvas/permissions/{agent_id}`
  (`tinyagentos/routes/project_canvas.py:146`). The kill switch is
  human-only. An agent token must never be able to flip its own (or another
  agent's) canvas permission; leaving the path off the allowlist means the
  middleware 401s any Bearer attempt before the handler runs, exactly the
  posture PATCH-on-tasks has today
  (`tinyagentos/auth_middleware.py:56-57`).

The middleware, as everywhere else, only passes the request THROUGH; the
route layer does all verification (registry JWT + scope + project binding via
`check_agent_scope_for_project`).

### D3. Enforcement: the checkbox becomes the live kill switch

New helper in `tinyagentos/routes/project_canvas.py`, modeled line-for-line
on `_authorize_task_actor` (`tinyagentos/routes/projects.py:426-469`):

`_authorize_canvas_actor(request, pstore, project_id, required_scope)`
returns `(actor_id, is_agent, project)` or a JSONResponse:

- **Session path**: resolve `request.state.user_id`; apply
  `_get_owned_project` semantics (owner or admin, existence-hiding 404 for
  everyone else). Decision for USER sessions: a human needs no checkbox and
  no scope; the gate is project visibility. Today that means owner/admin,
  which is unchanged behavior for jay and simultaneously closes the
  any-authenticated-user hole documented above. The forward rule, recorded
  now for multi-user: any HUMAN project member may write; the
  `can_edit_canvas` flag governs agent principals only.
- **Agent path**: `check_agent_scope_for_project(request, required_scope,
  project_id)` with `canvas_read` for GET routes and `canvas_write` for
  POST/PATCH/DELETE. Project-mismatch 403 collapses to the existence-hiding
  404, as on tasks.
- **Agent writes additionally require the membership flag.** The write
  handlers pass `author_kind="agent", author_id=canonical_id` into the store,
  whose `_check_edit_permission`
  (`tinyagentos/projects/canvas/store.py:152-168`) already queries
  `project_members.can_edit_canvas` for exactly that pair. Two store fixes
  make it complete: call `_check_edit_permission` from `add_element` too
  (closing the create gap that exists even for the in-process tools), and
  keep update/delete as they are. Result: an agent write lands only when the
  token holds a live project-bound `canvas_write` grant AND
  `project_members.can_edit_canvas = 1` for that `canonical_id`. Default is
  OFF (`tinyagentos/projects/project_store.py:34`), so a freshly approved
  lead can read but not write until the human flips the checkbox in Members.
  The checkbox PATCH already publishes `canvas.permission_changed`
  (`tinyagentos/routes/project_canvas.py:160-168`); it finally means
  something.
- **Agent reads require `canvas_read` + membership.** The membership check
  for reads is a `project_members` row existing (any value of the flag);
  reads do not require the edit bit. A revoked membership therefore kills
  reads and writes at once.
- The checkbox PATCH itself gains the missing owner/admin gate (session
  only, `_get_owned_project`).

Two principals, one matrix:

| Actor                          | list/stream/snapshots     | create/update/delete            |
|--------------------------------|---------------------------|----------------------------------|
| Human session (owner or admin) | yes                       | yes                              |
| Human session (other)          | 404 (existence-hiding)    | 404 (existence-hiding)           |
| Agent token, bound project     | `canvas_read` + member row | `canvas_write` + `can_edit_canvas=1` |
| Agent token, other project     | 404 (existence-hiding)    | 404 (existence-hiding)           |
| Agent token, no canvas scope   | 403                       | 403                              |

### D4. Honest attribution

- Agent-authored elements record `author_kind="agent"`,
  `author_id=<canonical_id>`. The schema already carries both columns
  (`tinyagentos/projects/canvas/store.py:19-20`); the UI can render an agent
  chip on the element without a migration.
- Fix `_user_id` to read `request.state.user_id`
  (`tinyagentos/routes/project_canvas.py:24-28`), so human-authored elements
  stop being attributed to `"system"`. This is a one-line correctness fix
  that this design depends on: an attribution model is worthless if the user
  half is fabricated.
- Agents remain restricted to `_AGENT_ALLOWED_KINDS` (no `user_shape`),
  enforced in the store today (`tinyagentos/projects/canvas/store.py:106`).
- The kind restriction and the permission check both key off `author_kind`,
  which the ROUTE now sets from the verified principal, never from the
  request body. An agent cannot claim to be a user; `_resolve_actor` on tasks
  established the invariant (`tinyagentos/routes/projects.py:472-488`) and
  canvas inherits it structurally.

### D5. Lead identity minting and credential retirement

The three leads become registry-minted identities exactly like grok and kilo,
via the existing consent flow, not a new mechanism:

1. Each lead fires `POST /api/agents/auth-requests` with
   `identity_claim` of its handle (for example `@taOSmd-dev`), `framework`
   naming its harness, `requested_scopes = [a2a_send, a2a_receive,
   project_tasks, canvas_read, canvas_write]`, and its `project_id`.
2. Jay approves from the phone consent picker, choosing the project (the
   picker is mandatory for project-bound scopes, D1). Approval mints the
   canonical id (for example `taosmd-dev-20260715-091500`), issues the
   project-bound JWT, writes the five grants, adds the `project_members` row,
   and syncs the a2a channel, all existing behavior
   (`tinyagentos/routes/agent_auth_requests.py:319-393`).
3. Jay flips the canvas checkbox ON for the lead in the Members screen. The
   checkbox is now load-bearing (D3).

Why the consent flow and not `seed-internal`: the internal path
(`tinyagentos/routes/agent_registry.py:373`) mints identity + a2a scopes but
adds NO project membership row and no project-bound grants by default, so the
canvas flag would have nothing to attach to; the consent flow produces the
complete shape (identity + project-bound grants + membership + channel) in
one approval, is already the path grok and kilo took, and keeps every lead in
the same governance audit trail. `mint-internal` remains available as an
admin escape hatch and its `_ALLOWED_SCOPES` gains the canvas scopes for
parity (D1).

One verified wrinkle the migration must handle: consent-minted records have
`handle=""`, and a2a send 403s an agent with no handle
(`tinyagentos/routes/a2a_bus.py:171-175`). Additionally, the lead handles may
ALREADY exist as seeded internal identities
(`tinyagentos/routes/agent_registry.py:170-175`). The migration therefore
supersedes: suspend the old seeded row (freeing the handle), then set the
handle on the new consent-minted identity (the store supports it,
`tinyagentos/agent_registry_store.py:573-605`; slice 6 adds the small admin
surface). Supersede-not-mutate matches the reattach rule in
`docs/design/external-agent-onboarding.md`.

**Retirement.** Once a lead's minted identity passes verification, its
user-account/password login is RETIRED for API work:

- The copied-credential logins (leads acting as jay) stop immediately.
- The interim website-dev USER account (created 2026-07-12 via the invite
  flow) is deprecated once the @taOS-website-dev minted identity works, then
  removed. jay remains the only human account.

**Migration checklist, per lead** (run for @taOS-dev, @taOSmd-dev,
@taOS-website-dev in turn):

1. Registry check: `GET /api/agents/registry` for an existing active row
   holding the lead's handle. If present (seeded), plan the supersede.
2. Lead fires the consent request (scopes + project as above); Jay approves
   from the phone with the project picked.
3. Verify identity: status poll returns `canonical_id` + token; token is
   stored on the lead's host in its gitignored token file (the
   onboarding doc's storage convention), never in a repo.
4. Suspend the old seeded identity (if any); set the handle on the new one.
5. Verify a2a: `POST /api/a2a/bus/send` with the lead token posts as its own
   handle; read back via `/api/a2a/bus/messages`.
6. Verify board: list tasks, claim and release a scratch task, comment.
7. Flip the canvas checkbox ON in Members; verify a canvas note create,
   patch, delete round-trip as the agent; verify the element shows
   `author_kind="agent"` with the canonical id.
8. Flip the checkbox OFF; verify writes 403 and reads still work.
9. Retire the credential: for website-dev, disable then delete the interim
   user account; for the others, rotate jay's password last so any residual
   copied credential dies with it.

### D6. Limits on agent canvas writes

Verified: no payload size cap and no write rate limit exist today. Agents
are exactly the callers that can emit a pathological loop, so writes get
bounds (applied to BOTH principals, humans included, generously):

- Serialized `payload` cap of 64 KiB per element, checked in the route before
  the store call, 413 on breach. Covers create and patch.
- Per-principal fixed-window rate limit on agent writes: 60 writes per
  minute per canonical_id per project, 429 on breach, reusing the fixed
  window pattern already in the tree (login limiter in `routes/auth.py`,
  pairing claim limiter in `routes/cluster.py`). Human sessions are not rate
  limited in v1.
- `snapshot.png` renders and writes a file server-side on every call
  (`tinyagentos/routes/project_canvas.py:127-133`); agent calls share the
  same 60/min window so a polling agent cannot turn the renderer into a disk
  hose.

## Slice plan

Numbered, PR-sized, ordered. Slices 1 to 5 are bounded and specified tightly
enough for external CLI coding agents. Slice 6 is operational
(maintainer-executed on the live box).

1. **Scope vocabulary.**
   `tinyagentos/routes/agent_auth_requests.py` (`VALID_SCOPES`, the
   project-required guard extended to the canvas scopes),
   `tinyagentos/routes/agent_registry.py` (`_ALLOWED_SCOPES`),
   `desktop/src/components/ConsentActions.tsx` (checkbox labels for the two
   new scopes). Tests: `tests/test_routes_agent_auth_requests.py` additions
   (request + approve with canvas scopes, reject canvas grant without
   project, narrow-not-widen still holds).
   Verify: `python -m pytest tests/test_routes_agent_auth_requests.py tests/test_agent_internal_mint.py -q`
2. **Middleware allowlist.**
   `tinyagentos/auth_middleware.py`: `_AGENT_CANVAS_ROUTES`,
   `_is_agent_canvas_path`, dispatch branch. Tests:
   `tests/test_auth_middleware.py` additions proving each listed
   method+path passes a Bearer through, the permissions PATCH does NOT, and
   near-miss paths (extra segments, wrong method, `/canvas/elements/x/y`)
   stay session-only.
   Verify: `python -m pytest tests/test_auth_middleware.py -q`
3. **Route gating + attribution.**
   `tinyagentos/routes/project_canvas.py`: `_authorize_canvas_actor`, wire
   all seven routes, fix `_user_id`, owner/admin gate on the permissions
   PATCH; `tinyagentos/projects/canvas/store.py`: permission check in
   `add_element`. Tests: `tests/test_routes_project_canvas.py` additions
   covering the full matrix in D3 (agent read with/without scope, write
   with/without checkbox, cross-project 404, author fields on agent-authored
   elements, user attribution no longer `"system"`).
   Verify: `python -m pytest tests/test_routes_project_canvas.py tests/projects -q`
4. **Stream + snapshot gating.**
   Same file: `canvas_read` on `/canvas/stream`, `snapshot.png`,
   `snapshot.tldr` for agent principals, plus the keepalive-tick recheck
   decision from the edge cases below. Tests: SSE connect as agent with and
   without scope, snapshot fetch as agent, snapshot stays read-scope (a
   `canvas_write`-only token is 403 on snapshots).
   Verify: `python -m pytest tests/test_routes_project_canvas.py -q -k "stream or snapshot"`
5. **Limits.**
   Payload size cap + agent write rate limit in
   `tinyagentos/routes/project_canvas.py`. Tests: 413 on oversized payload
   (create and patch), 429 past the window, human writes unaffected by the
   agent limiter.
   Verify: `python -m pytest tests/test_routes_project_canvas.py -q -k "limit or payload"`
6. **Lead minting + retirement runbook (operational, maintainer).**
   Execute the D5 checklist per lead on the live controller; the only code
   in this slice is the small admin surface for supersede (suspend row +
   set handle on the successor, exposed via `taosctl` or a registry route,
   maintainer's call at implementation time). Acceptance: all three leads
   working through their own tokens end to end, the interim website-dev user
   account deleted, jay's password rotated, and a screenshot of the Members
   screen showing the three leads with their canvas checkboxes.

Ordering: 1 and 2 in either order, 3 after both, 4 and 5 after 3, 6 last.

## Edge cases, honestly

- **Membership revoked mid-session.** Enforcement is per-request: the flag
  lookup in `_check_edit_permission` and the member-row lookup for reads hit
  `project_members` on every call, and removing the member deletes the row,
  so the next write OR read fails closed. There is no session cache to go
  stale. Grant revocation (suspend the agent, expire the grant) also lands on
  the next call via `check_agent_scope_for_project`.
- **Checkbox toggled off while an SSE stream is open.** The flag governs
  WRITES; a live read stream is `canvas_read` and is unaffected by the
  toggle, which is correct. The sharper case is membership removal or agent
  suspension while a stream is open: the stream was authorized at connect
  and would otherwise live until disconnect. Decision: re-verify the agent
  principal on each keepalive tick (every 10 seconds,
  `tinyagentos/routes/project_canvas.py:183-185`), closing the stream when
  the recheck fails. One registry + membership lookup per 10s per stream is
  cheap, and it bounds revocation latency to the keepalive interval.
- **Snapshot endpoints stay read-scope.** `snapshot.png` and `snapshot.tldr`
  are GETs that mutate nothing an agent controls (the PNG write is a
  server-side render artifact), so they require `canvas_read`, never
  `canvas_write`. The D6 rate window covers the render cost.
- **The create gap in the in-process tools.** Until slice 3 lands, the
  `mcp_tools` path can create elements for an agent with the checkbox off
  (verified: `add_element` never calls `_check_edit_permission`). The slice
  fixes the store, so both the HTTP path and the in-process path close at
  once; the fix must keep the tools' friendly permission-denied message
  (`tinyagentos/projects/canvas/mcp_tools.py:172-180`) working for create.
- **Consent-minted leads and the bus handle.** Without slice 6's handle step
  a freshly minted lead can do everything EXCEPT a2a send (403, "agent has no
  bus handle"). The checklist orders the supersede before the a2a
  verification for exactly this reason.
- **Element payload size.** Verified absent today; D6 adds it. 64 KiB is
  roomy for notes, mermaid sources and link metadata while keeping a runaway
  agent from writing megabyte blobs into `projects.db` rows the board then
  streams to every subscriber.
- **Nested-elements interaction.** When group-into-project absorbs the taOS
  trio, agent grants bound to the absorbed source projects hit the 409
  external-token guard in that design's migration. Minting the leads against
  the CURRENT projects therefore either happens after the grouping, or
  accepts a re-mint against the parent at grouping time. The checklist is
  written per-project and re-runnable, so re-minting is a repeat of steps 2
  through 8, not a new procedure.

## Open questions for Jay (each with a recommendation)

1. **Should tools (grok, kilo) ever get `canvas_read` for context?**
   Recommendation: yes, as an optional read-only grant, off by default,
   added per-project through the same consent picker when a lead's workflow
   genuinely needs the tool to see the board's visual context. Never
   `canvas_write`: a tool that wants to put something on the canvas hands it
   to its lead.
2. **Should canvas events on the stream carry author identity?**
   Recommendation: yes. `element_added` and `element_updated` already embed
   the full element including `author_kind`/`author_id`
   (`tinyagentos/projects/canvas/store.py:138, 206`), but
   `element_deleted` carries only the id (`:227-230`) and
   `permission_changed` names no actor. Add a uniform
   `actor: {kind, id}` field to every `canvas.*` event so the UI can show
   who did what live, matching the attribution posture of D4.
3. **Should the approve path set the registry handle from the identity
   claim?** Recommendation: yes, sanitized, with a 409 on collision with an
   active identity, so future consent-minted agents are a2a-complete without
   slice 6's manual handle step. Kept out of the bounded slices because the
   collision policy deserves its own small review.
4. **Should `canvas_write` imply `canvas_read`?** Recommendation: no
   implication; grants stay explicit and the closed vocabulary stays flat,
   matching how every existing scope pair (`memory_*`, `files_*`, `a2a_*`)
   is modeled. Leads simply request both.
5. **Element-scoped canvas claims.** The nested-elements design defines an
   additive `element_id` claim that could later narrow canvas access to one
   element's items. Recommendation: keep it roadmap exactly as that document
   staged it; nothing in this design blocks it, since the canvas gate keys
   on (project, canonical_id) and an element claim would only narrow further.

## Non-goals

- No new identity system, no new token format, no second minting path. The
  consent loop is the only birth canal for lead identities.
- No canvas access for unauthenticated callers, ever.
- No per-element canvas ACLs in v1 (open question 5 is the seam).
- No change to the human login system beyond retiring its misuse by agents.
