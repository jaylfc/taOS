# Lead-agent identities and external-agent canvas access (founding design)

Status: DECIDED (Jay, 2026-07-12; decisions folded into the design and the
slice plan). Plan only, no implementation.
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

## The org model: leads, tools, and orthogonal capability

This section is the durable statement of the structure the rest of the design
serves. Decided: **capability is fully orthogonal to role.** `canvas_read`
and `canvas_write` are grantable to ANY agent, lead or tool, both OFF by
default for everyone. Role says who is accountable; the capability checkboxes
say what each agent may touch. Leads and tools draw from the same checkboxes.

**Lead: an exclusive designation, not a capability bundle.**

- Exactly ONE lead per project (and, once the nested-elements design lands,
  one per element). The Members screen shows a Lead selector; picking a new
  lead unsets the previous one, and the server enforces the invariant (D7).
- Being lead grants NOTHING by itself. It is the org-role marker: the
  default assignee for the project (and later its elements, per the
  nested-elements assignment slice, `element.assignee_id` in
  `docs/design/projects-nested-elements.md`), displayed on the members list.
  A lead that needs canvas access gets it the same way a tool would: the
  human ticks its checkboxes.
- Today's `project_members.is_lead` flag (from #291) is NON-exclusive and
  only feeds the a2a quiet-filter bypass
  (`tinyagentos/projects/project_store.py:35`,
  `tinyagentos/projects/a2a.py:108-146`). The decided model supersedes it
  with a project-level pointer (D7); the quiet-filter behavior moves with it.

**Leads in practice** (@taOS-dev, @taOSmd-dev, @taOS-website-dev):

- Long-lived, registry-minted identities, one per lead, never per-session.
  Stable identity is the same decision the onboarding design locked for
  coding agents (`docs/design/external-agent-onboarding.md`, "identity is
  STABLE per (tool, project)").
- Members of the taOS project. Expected grants in practice: `project_tasks`
  (claim, release, close, reopen, comment), `a2a_send` + `a2a_receive`, and
  both canvas scopes with both checkboxes ticked by the human. None of that
  comes from the Lead designation itself.
- Each is the designated lead of its project today and of its element once
  the taOS trio is grouped into one project; this document supplies the
  identities the nested-elements assignment design expects to point at.

**Tools** (grok, kilo, and future executors):

- Everything that is not the designated lead. Task-scoped executors, invoked
  by leads or the orchestrator, not self-directed members of the org.
- Default posture: `project_tasks` for their bound project, nothing else.
  But the canvas checkboxes are available to them like any agent: a tool
  whose workflow genuinely needs the board gets `canvas_read` and/or
  `canvas_write` per-project through the same consent picker and Members
  checkboxes. Their identities remain consent-minted exactly as today.

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
  project, subject to the membership flag in D3.
- DECIDED: grants are explicit. `canvas_write` does NOT imply `canvas_read`;
  the closed vocabulary stays flat, matching every existing scope pair
  (`memory_*`, `files_*`, `a2a_*`). An agent that needs both requests both.
- DECIDED: both scopes are role-agnostic. Any agent, lead or tool, may
  request and be granted them; nothing in the vocabulary or the approve path
  keys on role.

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

### D3. Enforcement: the checkboxes become the live kill switches

DECIDED: the Members screen shows TWO per-agent checkboxes, canvas read and
canvas write, both off by default, for every agent member regardless of
role. `project_members` gains a `can_read_canvas` column (default 0) beside
`can_edit_canvas` (`tinyagentos/projects/project_store.py:34`), and the
permissions PATCH (`tinyagentos/routes/project_canvas.py:146`) sets either
flag.

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
  agent can do nothing on the canvas until the human flips checkboxes in
  Members. The checkbox PATCH already publishes `canvas.permission_changed`
  (`tinyagentos/routes/project_canvas.py:160-168`); it finally means
  something.
- **Agent reads require `canvas_read` + `can_read_canvas = 1`.** DECIDED:
  read is its own checkbox, off by default, not implied by membership and
  not implied by the write bit. A revoked membership deletes the row and
  therefore kills reads and writes at once.
- The checkbox PATCH itself gains the missing owner/admin gate (session
  only, `_get_owned_project`).

Two principals, one matrix:

| Actor                          | list/stream/snapshots     | create/update/delete            |
|--------------------------------|---------------------------|----------------------------------|
| Human session (owner or admin) | yes                       | yes                              |
| Human session (other)          | 404 (existence-hiding)    | 404 (existence-hiding)           |
| Agent token, bound project     | `canvas_read` + `can_read_canvas=1` | `canvas_write` + `can_edit_canvas=1` |
| Agent token, other project     | 404 (existence-hiding)    | 404 (existence-hiding)           |
| Agent token, no canvas scope   | 403                       | 403                              |

Role never appears in the matrix. A lead with no checkboxes ticked has no
canvas access; a tool with both ticked has full access. That is decision 1.

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
- DECIDED: every `canvas.*` stream event carries a uniform
  `actor: {kind, id}` field, deletes included. `element_added` and
  `element_updated` already embed the element's `author_kind`/`author_id`
  (`tinyagentos/projects/canvas/store.py:138, 206`), but `element_deleted`
  carries only the id (`:227-230`) and `permission_changed` names no actor.
  All four carry the field (redundant on the two element events, uniform for
  consumers), so the UI can show who did what live. Lands in slice 3.

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
3. Jay flips canvas read AND canvas write ON for the lead in the Members
   screen (both load-bearing per D3, neither implied by the other), and sets
   the agent as the project Lead via the selector (D7).

Why the consent flow and not `seed-internal`: the internal path
(`tinyagentos/routes/agent_registry.py:373`) mints identity + a2a scopes but
adds NO membership row and no project-bound grants, so the canvas flags would
have nothing to attach to; the consent flow produces the complete shape
(identity + project-bound grants + membership + channel) in one approval, is
the path grok and kilo took, and keeps every lead in the same governance
audit trail. `mint-internal` stays as an admin escape hatch and its
`_ALLOWED_SCOPES` gains the canvas scopes for parity (D1).

The handle wrinkle, now DECIDED and fixed at the source: consent-minted
records have historically gotten `handle=""`, and a2a send 403s an agent with
no handle (`tinyagentos/routes/a2a_bus.py:171-175`). The approve path will
set the registry handle from the sanitized identity claim, 409 on collision
with an active identity so the approver picks a variant (bounded slice 7);
every consent-minted agent is then a2a-complete at approval. The lead handles
may ALREADY be held by seeded internal identities
(`tinyagentos/routes/agent_registry.py:170-175`), so the migration suspends
the old seeded row FIRST, freeing the handle; approving before the suspend
simply 409s and you suspend then retry. Manual handle-set on the store
(`tinyagentos/agent_registry_store.py:573-605`) remains the fallback for any
mint that predates slice 7. Supersede-not-mutate matches the reattach rule in
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
   holding the lead's handle. If present (seeded), suspend it now so the
   handle is free for the approve path to claim.
2. Lead fires the consent request (scopes + project as above); Jay approves
   from the phone with the project picked. Approval sets the handle from the
   identity claim (slice 7); a 409 means step 1 was skipped.
3. Verify identity: status poll returns `canonical_id` + token; token is
   stored on the lead's host in its gitignored token file (the
   onboarding doc's storage convention), never in a repo.
4. If the mint predates slice 7: set the handle manually on the new identity
   (fallback path above).
5. Verify a2a: `POST /api/a2a/bus/send` with the lead token posts as its own
   handle; read back via `/api/a2a/bus/messages`.
6. Verify board: list tasks, claim and release a scratch task, comment.
7. In Members: set the agent as project Lead (verify any previous lead is
   unset), flip canvas read AND write ON; verify a note create, patch,
   delete round-trip as the agent and the element shows
   `author_kind="agent"` with the canonical id.
8. Flip write OFF; verify writes 403 and reads still work. Flip read OFF;
   verify list and stream fail too.
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

### D7. The Lead designation: exclusive, server-enforced

DECIDED: one lead per project, and one per element once the nested-elements
design lands. The designation is pure org-role (default assignee, members
list display, quiet-filter bypass); it grants no capability.

**Storage: a project-level `lead_member_id` column on `projects`,** not the
existing per-member `is_lead` flag. Rationale: the invariant is 1-per-project,
so a single pointer column makes it structural: two leads are unrepresentable,
setting a new lead is one UPDATE that atomically unsets the previous one, and
there is no unset-the-old-row transaction or partial unique index to get
wrong.

- `projects` gains `lead_member_id TEXT NULL` referencing a member of that
  project (validated in the route, not by FK, matching the store's style in
  `tinyagentos/projects/project_store.py`).
- New endpoint `PATCH /api/projects/{project_id}/lead` with body
  `{"member_id": "<id>" | null}`, session-only (owner or admin, same gate as
  the members routes), 404 for a member id not in the project. It replaces
  the per-member route `PATCH /api/projects/{pid}/members/{mid}/lead`
  (`tinyagentos/routes/projects.py:298`) and `set_member_lead`
  (`tinyagentos/projects/project_store.py:80-84`), which are removed with
  their UI call site.
- Consumers move: the a2a quiet-filter and channel `settings.leads` sync
  currently select members `WHERE is_lead = 1`
  (`tinyagentos/projects/a2a.py:108-146`); they read `lead_member_id`
  instead (a list of at most one).
- Migration: backfill `lead_member_id` from `is_lead` where a project has
  exactly one flagged member; several flagged members backfill to NULL and
  the human picks in the UI (the old flag never promised exclusivity, so no
  auto-choice is safe). The `is_lead` column stays in place, unwritten and
  ignored, per the store's additive-migration style
  (`tinyagentos/projects/project_store.py:69-70`).
- Elements: when `docs/design/projects-nested-elements.md` lands, its
  `element.assignee_id` slot is the per-element equivalent (one member per
  element, default task routing). No second mechanism here; the project-level
  lead and the element-level assignee share the exclusive-pointer shape.

## Slice plan

Numbered, PR-sized, ordered. Slices 1 to 7 are bounded and specified tightly
enough for external CLI coding agents. Slice 8 is operational
(maintainer-executed on the live box).

1. **Scope vocabulary.**
   `tinyagentos/routes/agent_auth_requests.py` (`VALID_SCOPES`, the
   project-required guard extended to the canvas scopes),
   `tinyagentos/routes/agent_registry.py` (`_ALLOWED_SCOPES`),
   `desktop/src/components/ConsentActions.tsx` (checkbox labels for the two
   new scopes). No role check anywhere: the scopes are requestable by any
   agent. Tests: `tests/test_routes_agent_auth_requests.py` additions
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
3. **Route gating + attribution + event actor.**
   `tinyagentos/routes/project_canvas.py`: `_authorize_canvas_actor`, wire
   all seven routes, fix `_user_id`, owner/admin gate on the permissions
   PATCH, permissions PATCH extended to set either flag;
   `tinyagentos/projects/project_store.py`: `can_read_canvas` column
   (additive migration, default 0);
   `tinyagentos/projects/canvas/store.py`: permission check in
   `add_element`, uniform `actor: {kind, id}` on every published `canvas.*`
   event including `element_deleted` and `permission_changed` (D4). Tests:
   `tests/test_routes_project_canvas.py` additions covering the full D3
   matrix (agent read with/without scope and read flag, write with/without
   write flag, write flag does not grant read, cross-project 404, author
   fields on agent-authored elements, user attribution no longer `"system"`,
   actor present on delete events).
   Verify: `python -m pytest tests/test_routes_project_canvas.py tests/projects -q`
4. **Stream + snapshot gating.**
   Same file: `canvas_read` on `/canvas/stream`, `snapshot.png`,
   `snapshot.tldr` for agent principals, plus the keepalive-tick recheck
   from the edge cases below (which now also covers the `can_read_canvas`
   flag). Tests: SSE connect as agent with and without scope and flag,
   snapshot fetch as agent, snapshot stays read-scope (a
   `canvas_write`-only token is 403 on snapshots), stream closes after the
   read flag is cleared.
   Verify: `python -m pytest tests/test_routes_project_canvas.py -q -k "stream or snapshot"`
5. **Limits.**
   Payload size cap + agent write rate limit in
   `tinyagentos/routes/project_canvas.py`. Tests: 413 on oversized payload
   (create and patch), 429 past the window, human writes unaffected by the
   agent limiter.
   Verify: `python -m pytest tests/test_routes_project_canvas.py -q -k "limit or payload"`
6. **Members UI: capability checkboxes + exclusive Lead designation (D7).**
   Backend: `tinyagentos/projects/project_store.py` (`projects` gains
   `lead_member_id`, backfill from single-flagged `is_lead`, retire
   `set_member_lead`), `tinyagentos/routes/projects.py` (new
   `PATCH /api/projects/{project_id}/lead`, remove the per-member lead
   route at `:298`), `tinyagentos/projects/a2a.py` (quiet-filter and
   `settings.leads` sync read the column). Frontend:
   `desktop/src/apps/ProjectsApp/ProjectMembers.tsx` (second checkbox
   "Can read canvas" beside "Can edit canvas"; the Lead checkbox becomes an
   exclusive selector driven by `lead_member_id`),
   `desktop/src/lib/projects.ts` (project-level `setLead`),
   `desktop/src/apps/ProjectsApp/canvas/canvas-api.ts` (`setPermission`
   gains the flag argument). Tests: `tests/test_routes_projects.py` (set
   lead A then B leaves only B, clear to null, 404 on non-member,
   session-only), `tests/projects/test_a2a.py` (quiet filter follows the
   column), `ProjectMembers.test.tsx` (selector exclusivity, read checkbox).
   Verify: `python -m pytest tests/test_routes_projects.py tests/projects -q`
   and the desktop test run for `ProjectMembers.test.tsx`.
7. **Consent handle fix (decision 3).**
   `tinyagentos/routes/agent_auth_requests.py` approve path: set the
   registry handle from the sanitized identity claim
   (store setter at `tinyagentos/agent_registry_store.py:573-605`); return
   409 when the handle collides with an ACTIVE identity so the approver
   picks a variant, leaving the request pending. Suspended rows do not
   block. Tests: `tests/test_routes_agent_auth_requests.py` (approve sets
   the handle, minted agent passes the a2a no-handle gate, 409 on collision
   with an active identity, approve succeeds after the holder is suspended).
   Verify: `python -m pytest tests/test_routes_agent_auth_requests.py -q`
8. **Lead minting + retirement runbook (operational, maintainer).**
   Execute the D5 checklist per lead on the live controller; the only code
   is the small admin surface for suspending a seeded row (`taosctl` or a
   registry route, maintainer's call). Acceptance: all three leads working
   through their own tokens end to end, the interim website-dev user account
   deleted, jay's password rotated, and a Members-screen screenshot showing
   the three leads with both canvas checkboxes on and each project's Lead
   set.

Ordering: 1 and 2 in either order, 3 after both, 4 and 5 after 3, 6 after 3
(its read checkbox drives the extended permissions PATCH), 7 independent of
3 to 6, 8 last (it depends on 6 and 7).

## Edge cases, honestly

- **Membership revoked mid-session.** Enforcement is per-request: the flag
  lookup in `_check_edit_permission` and the member-row lookup for reads hit
  `project_members` on every call, and removing the member deletes the row,
  so the next write OR read fails closed. There is no session cache to go
  stale. Grant revocation (suspend the agent, expire the grant) also lands on
  the next call via `check_agent_scope_for_project`.
- **Checkbox toggled off while an SSE stream is open.** The write flag does
  not touch a live read stream, correctly. But the READ flag now governs
  streams, and so do membership removal and agent suspension; a stream
  authorized at connect would otherwise live until disconnect. Decision:
  re-verify the agent principal, including `can_read_canvas`, on each
  keepalive tick (every 10 seconds,
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
- **Consent-minted leads and the bus handle.** Before slice 7 lands, a
  freshly minted lead can do everything EXCEPT a2a send (403, "agent has no
  bus handle") until its handle is set manually. The checklist orders the
  seeded-row suspend before approval so the approve-path handle claim (or
  the manual fallback) lands before the a2a verification.
- **Element payload size.** Verified absent today; D6 adds it. 64 KiB is
  roomy for notes, mermaid sources and link metadata while keeping a runaway
  agent from writing megabyte blobs into `projects.db` rows the board then
  streams to every subscriber.
- **Nested-elements interaction.** When group-into-project absorbs the taOS
  trio, agent grants bound to the absorbed source projects hit the 409
  external-token guard in that design's migration, so minting the leads
  against the CURRENT projects either happens after the grouping or accepts
  a re-mint against the parent at grouping time. The checklist is
  per-project and re-runnable: a re-mint repeats steps 2 through 8.

## Decisions (Jay, 2026-07-12)

The five open questions from the draft, now decided and folded in above:

1. **Capability is orthogonal to role.** `canvas_read` and `canvas_write`
   are grantable to ANY agent, tool or lead, all off by default, via
   per-agent Members checkboxes. Lead is a separate, exclusive designation
   granting no capability by itself. Goes further than the draft, which had
   reserved write to leads. Org-model section, D3 and D7; slices 1, 3 and 6.
2. **Uniform event actor: yes.** Every `canvas.*` stream event carries
   `actor: {kind, id}`, deletes and permission changes included. D4; slice 3.
3. **Approve path sets the registry handle: yes.** Sanitized from the
   identity claim, 409 on collision with an active identity so the approver
   picks a variant. Fixes the `handle=""` a2a 403 for consent-minted agents
   at the source. Promoted from a deferred item to bounded slice 7.
4. **`canvas_write` does not imply `canvas_read`.** Grants stay explicit and
   the vocabulary flat. D1; the D3 matrix and slice 3 tests pin it.
5. **Element-scoped canvas claims stay roadmap.** Unchanged from the draft:
   the nested-elements `element_id` claim can later narrow canvas access,
   nothing here blocks it, and nothing here builds it.

## Non-goals

- No new identity system, no new token format, no second minting path. The
  consent loop is the only birth canal for lead identities.
- No canvas access for unauthenticated callers, ever.
- No per-element canvas ACLs in v1 (decision 5 keeps that seam on the
  roadmap).
- No change to the human login system beyond retiring its misuse by agents.
