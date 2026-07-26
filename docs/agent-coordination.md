# Working the repo in parallel (coordination discipline)

taOS is often built by several contributors (and automated agents) at the same
time. This doc is the shared discipline that keeps that parallel work from
stepping on itself: predictable branches, one change per PR, isolated working
copies, and a CI gate that never relies on stale results. Follow it whether you
are a person or an agent.

## Branch from the integration branch

- `dev` is the integration branch; `master` is release-only and gated by the
  maintainer. Never commit or merge to `master` directly.
- Always branch from the latest `origin/dev`: `git fetch origin` first, then cut
  your branch from `origin/dev` (not a possibly-stale local `dev`). Building on a
  stale base is the most common cause of avoidable merge conflicts.
- Small, low-risk fixes may go straight to `dev`. Anything larger, multi-commit,
  or worth review goes through a pull request so CI runs.

## One change per branch, one branch per PR

- Keep each branch and PR to a single logical change. A focused diff reviews
  faster and conflicts less.
- Make surgical edits: touch only what the change requires. Do not reformat or
  refactor adjacent code, and do not edit lockfiles (uv.lock, package-lock,
  etc.) incidentally. If you notice unrelated dead code, mention it rather than
  deleting it in the same PR.
- Every changed line should trace back to the change you set out to make.

## Isolate parallel work with worktrees

When two tasks run at once, give each its own working copy so they cannot
collide on the index or the working tree:

```
git fetch origin
git worktree add ../taos-<task> -b feat/<task> origin/dev
```

- One worktree per task; never reuse another contributor's worktree or branch.
- Remove the worktree when the branch is merged (`git worktree remove`).
- Do not force-push a branch that is under review; others may have it checked
  out, and a force-push invalidates in-progress review.

## Claim before you build

- For tracked work, claim the task (assign it / mark it in progress) before
  starting, so two contributors do not build the same thing twice.
- One task maps to one branch maps to one PR. If a task grows, split it rather
  than letting the PR sprawl.

## Gate on fresh CI, not stale rollups

- Open the PR against `dev` and let the required checks run: the Python test
  matrix (3.12 and 3.13), the SPA build, and lint.
- Merge only when the required checks are green on the current head of the
  branch. Never merge on a stale or partial check rollup, and never merge while
  a required job is still pending.
- Fold every must-fix review finding (a real bug, a security issue, or an
  edge-case correctness problem) before merging. Style and preference nits can
  be deferred or taken in a follow-up.

## Keep the shared docs honest

- When you merge something others depend on, update the relevant README or
  docs in the same PR so the next contributor is not working from a stale map.
- If your change makes an existing doc inaccurate, fix the doc in the same PR.

## Posting to the coordination bus (a2a)

Agents coordinate over the A2A bus. When you post, the send body is
`{from, thread, body}`, where the destination field is **`thread`**, not `channel`:

```
POST <bus>/a2a/send
{"from": "@you", "thread": "taOS-taOSmd-hermes-integration", "body": "..."}
```

- A `channel` field is silently ignored: the post still succeeds (200 with an
  id) but lands in the default `general` thread, where the agent you addressed
  is not looking. It is an easy mistake precisely because nothing errors.
- After posting to a specific thread, verify it landed there (read the thread
  back and confirm your message id is present) before assuming it was delivered.

The raw bus above is unauthenticated on the LAN and trusts the `from` field, so
reaching it means either the owner's account or an SSH hop. A registered agent
should instead post through the controller's authenticated proxy, which forces
`from` to the agent's own registry handle (no spoofing), so it posts as itself,
not the owner:

```
POST <controller>/api/a2a/bus/send   (Authorization: Bearer <registry JWT, scope a2a_send>)
{"thread": "build", "body": "...", "reply_to": <id>?}
```

An admin session may also call it and set an explicit `from`. On a bus failure
the proxy returns 502 (the read proxies degrade to an empty 200 instead).

## Agent API surface (scoped registry JWT)

A registered external agent authenticates with its registry JWT
(`Authorization: Bearer`) and reaches exactly the routes its granted SCOPES
allow, nothing else: the middleware allowlist is a closed set, no skeleton key.
The surface, by scope:

- **project_tasks** (the kanban board): `GET /api/projects/{pid}/tasks`,
  `.../tasks/ready`, `.../tasks/{id}`, `.../tasks/{id}/comments` (GET + POST),
  `POST .../tasks/{id}/(claim|release|close|reopen)`, and
  `GET /api/projects/tasks/{id}/context`. This is read + lifecycle + comments
  only. Granting project_tasks also makes the agent a project member.
- **project_tasks_create**: `POST /api/projects/{pid}/tasks` (author new cards).
  This is a SEPARATE scope from project_tasks and is off by default; grant it
  explicitly when an agent needs to create cards.
- **canvas_read**: `GET .../canvas/elements`, `.../canvas/snapshot.png|.tldr`,
  `.../canvas/stream`. **canvas_write**: `POST .../canvas/elements`,
  `PATCH|DELETE .../canvas/elements/{id}`.
- **files_read**: `GET /api/projects/{slug}/files` (list), `.../files/watch`,
  `GET .../files/{path}` (download), `.../trash`, `.../stats`. **files_write**:
  `POST .../files/upload` (multipart), `POST .../mkdir`, `DELETE .../files/{path}`,
  and the trash restore/purge/empty routes. NOTE: the files routes key on the
  project SLUG in the path, not the id.
- **decisions_write**: `POST /api/decisions` (raise a human-in-the-loop
  decision). Listing/answering decisions stays session-only.
- **a2a_send / a2a_receive**: the authenticated bus proxy above
  (`/api/a2a/bus/send|messages|channels|stream`), which forces `from` to the
  agent's own handle.

Access is per-project: a token is authorized for a project only when the agent
holds an active grant + membership there; a request for a project it has no
grant on returns an existence-hiding 404. External agents onboard via the
consent flow (`POST /api/agents/auth-requests`) or a project invite (link +
PIN; see issue #1780). When you change the agent allowlist in
`tinyagentos/auth_middleware.py`, update this section in the same PR. Note the
doc-gate only fires on files ADDED or DELETED, not edits to an existing file,
so it will NOT catch allowlist drift here on its own; keep this list in sync by
hand.

Multi-project identities (taOS #1862): one agent identity (the registry JWT) may
belong to several projects at once. The grants table keys a grant on
`(canonical_id, scope, project_id)`, so the same scope can be held for multiple
projects. Project access is GRANT-GATED, not claim-gated: the token's `project_id`
claim is advisory only, and `check_agent_scope_for_project` authorizes a project
purely from a matching active grant. An already-registered agent is added to a
further project via `POST /api/projects/{project_id}/members/assign-agent`
(admin/owner gated) or by redeeming an invite whose handle collides with an
active identity (the existing canonical_id and token are reused instead of
409ing).

## Requesting more scope for an existing identity (scope requests)

The auth-request flow (`POST /api/agents/auth-requests`) MINTS A NEW identity on
approval, so an agent that already holds an active registry identity cannot use
it to gain more scopes without duplicating itself. A scope request adds grants to
that SAME canonical_id instead:

- `POST /api/agents/registry/{canonical_id}/scope-requests`
  `{requested_scopes, project_id?, reason?}`: create a pending request. Unlike
  the new-agent auth-request (unauthenticated, since the agent has no creds yet),
  this is CREDENTIALLED: the caller must be the agent's OWN registry bearer token
  (`sub` == `canonical_id`) OR the owning user / an admin. An anonymous caller can
  never escalate an existing identity. The middleware allowlist exposes only the
  create path to a registry JWT; the route re-checks identity == canonical_id.
- `POST /api/agents/registry/{canonical_id}/scope-requests/{req_id}/approve`
  `{granted_scopes, project_id?}`: owner/admin only. The admin may narrow but
  never widen the requested scopes; each granted scope is added via
  `add_grant(canonical_id, scope, project_id)` (idempotent on the
  `(canonical_id, scope, project_id)` UNIQUE key, so re-approving is a no-op). No
  new identity is created.
- `POST /api/agents/registry/{canonical_id}/scope-requests/{req_id}/deny`:
  owner/admin only.

Requested scopes are validated against the same closed `VALID_SCOPES` vocabulary
as the consent flow. `project_tasks` and the canvas scopes still require an
explicit `project_id`; `decisions_read` / `decisions_write` (and the other global
scopes) may be granted globally (`project_id=None`) or per-project. Creation
surfaces a bell notification (`source: agent_scope_requests`) to the owner/admin,
retired when the request is decided.

## Project invite redeem route (link + PIN)

A project invite lets an external agent join without going through the consent
UI. The mint dialog (admin, in the project's Members panel) creates the invite;
the agent redeems it. Two endpoints are auth-EXEMPT (the PIN is the proof of
possession), added method-sensitively to `tinyagentos/auth_middleware.py` exactly
like `POST /api/cluster/pairing/claim`, and per-IP rate-limited (20 requests per
10s, reusing the pairing throttle helper):

- `POST /api/projects/invites/redeem`: body `{invite_id, pin, harness, label?}`.
  Verifies the PIN (wrong PIN / expired / attempt-capped -> 403; already redeemed
  / revoked -> 409), derives the agent handle `{project_slug}-{harness}[-{label}]`
  and de-dupes it against active registry agents in the project, then either
  auto-approves through the shared `approve_request_record` helper (decided_by =
  the invite's creator) or leaves the request pending (manual mode, consent bell
  fires). Returns a connection bundle plus `{request_id, agent_handle, poll_path}`.
  `project_tasks` is force-included so a successful redeem always yields a project
  member.
- `GET /i/{invite_id}`: content-negotiated advert. An agent requesting
  `Accept: application/json` gets the redeem contract (`{method, path, fields}`);
  a browser gets a minimal HTML page. No PIN check here; it only advertises the
  contract.

The redeem response carries a **connection bundle** (design section 4 plus the
Approved-build addendum). It contains NO token or secret (the token still
arrives via the status poll). The bundle has:

- `controller.endpoints`: the controller's reachable addresses: non-loopback
  LAN IPv4s (priority ordered, operator override first) and the mesh (Tailscale)
  node IP when joined. No relay in Phase 1.
- `apis`: the agent-JWT-reachable surface, scoped EXACTLY to the granted scopes
  and mirroring the middleware canvas allowlist so the advertised routes are the
  ones the token can call: task routes when `project_tasks` is granted; canvas
  `GET /canvas/elements` + `/canvas/snapshot.png` only when `canvas_read` is
  granted; canvas `POST /canvas/elements` + `PATCH|DELETE /canvas/elements/{id}`
  only when `canvas_write` is granted; the A2A bus proxy (`/api/a2a/bus/send|
  messages|channels`) whenever an `a2a_*` scope is granted.
- `delivery`: the timed-check contract (`poll_path`, `stream_path`,
  `check_interval_secs` from the invite, `cursor: ts`, `filter: mentions+project`).
- `onboarding` + `guide_markdown`: a personalized capability guide (repo link,
  agent manual links, scoped Projects/Canvas summary, the A2A authenticated-proxy
  contract, and explicit instructions to write the identity/project/token-file/bus
  contract into the agent's OWN memory and to poll every `check_interval_secs`).

See `docs/design/external-agent-project-invite.md` (issue #1780) for the full
design; the bundle advertises canvas routes only when the corresponding scope
was actually granted.

These rules are deliberately lightweight. The goal is not process for its own
sake; it is to let many hands move quickly on the same codebase without undoing
each other's work.
