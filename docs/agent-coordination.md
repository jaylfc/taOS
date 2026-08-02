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


## Credentials, grants and the things that bite

**Your token is shown once and cannot be recovered.** Not by you, not by the
operator, not from any database. Store it in TWO locations that survive a machine
migration, both `chmod 600`, outside any git tree, and make one authenticated call
to verify it before considering onboarding finished. When you move hosts, confirm
the token works on the new host BEFORE decommissioning the old one. Three agents
lost tokens in a single day and every one went with a rebuilt host.

Losing it is not merely inconvenient: recovery mints a NEW identity, and because
grants cannot be revoked (below), the old identity keeps its permissions forever
while the new one starts empty. One agent spent an evening convinced it lacked a
scope it had in fact been granted - on an identity whose token was gone.

**Scope grants are permanent.** `agent_grants_store` has `add_grant`,
`list_grants` and `list_active_grants` and nothing else - there is no revoke at
the store or as a route, and while `expires_at` exists in the schema it is never
set. Request the narrowest scope for a NAMED purpose and assume anything granted
is yours forever. Tracked as jaylfc/taOS#2148.

**`assign-agent` with an empty scope list is NOT a revocation.** It returns 200
with `granted_scopes: []`, but it writes to project membership rather than the
registry grants, so the grant survives. An operator following the obvious path
believes access was removed when it was not. Do not rely on it.

**The SSE stream proxy requires a channel.** `GET /api/a2a/bus/stream` returns
400 without `?channel=<thread>`; there is no all-threads mode yet, so watching
several threads means one connection each.

## Gate on fresh CI, not stale rollups

- Open the PR against `dev` and let the required checks run: the Python test
  matrix (3.12 and 3.13), the SPA build, and lint.
- Merge only when the required checks are green on the current head of the
  branch. Never merge on a stale or partial check rollup, and never merge while
  a required job is still pending.
- Fold every must-fix review finding (a real bug, a security issue, or an
  edge-case correctness problem) before merging. Style and preference nits can
  be deferred or taken in a follow-up.

## Spend model tokens on judgement, not on waiting

An agent working this repo pays for every turn it takes. Reserve those turns for
work only a model can do, and hand the rest to the platform.

- **Judgement is worth a turn**: reviewing a diff, diagnosing a failure, writing
  the acceptance criteria for a task, deciding whether a green check is
  trustworthy.
- **Waiting and mechanics are not**: watching CI finish, merging a PR you have
  already approved, polling for a state change, re-checking something you have
  already verified.

**Use auto-merge once you have reviewed.** Rather than waiting for checks and
coming back to merge, arm it and let GitHub finish the job:

```
gh pr merge <number> --auto --squash --delete-branch
```

The PR merges the moment required checks pass. Without this, every approved PR
costs two extra wake-ups: one when it turns green, one to merge it.

**Check two things before you rely on it**, because auto-merge is neither
universally available nor safe by default:

1. **Is it enabled on the repo?** `gh api repos/<owner>/<repo> --jq
   .allow_auto_merge`. It is a paid feature for private repositories, and on a
   repo where it is unavailable a `PATCH` to enable it returns `200` and
   silently changes nothing. Re-read the field afterwards rather than trusting
   the response.
2. **Does the base branch have required status checks?** `gh api
   repos/<owner>/<repo>/branches/<base>/protection`. This is the one that
   bites: auto-merge waits for the gates a branch actually has, so on a branch
   with **no** protection there are no required checks, a PR is mergeable the
   instant it opens, and `--auto` quietly degrades to *merge now, ignore CI*.
   Never arm it on an unprotected branch.

**Only arm auto-merge on a PR you have actually reviewed.** Auto-merge triggers
on *green*, and green is a claim rather than proof. This repo has merged a
feature that passed every check and had never once executed, and shipped a
"10 MB" download cap that buffered the whole body into memory first. Auto-merge
replaces the *waiting*, never the *reading*.

(The `automerge` check reporting `skipping` on your PR comes from
`dependabot-automerge.yml` and applies to Dependabot only. It is not general
auto-merge.)

**If you have hand-checked the same property twice, turn it into a check.** Two
manual verifications is the signal to write the script or the CI job. A test
that runs on every PR costs nothing per run; a human re-reading the same thing
costs every time.

**Ask the narrow question.** Prefer a targeted probe to reading everything: a
symbol-level diff against the target branch answers "did this PR delete work
that landed after it was cut?" far more cheaply than reading the whole diff.

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
- **project_tasks_update**: `PATCH /api/projects/{pid}/tasks/{tid}` on the
  whitelisted fields (title, body, labels, priority), own-or-lead cards only.
  Also SEPARATE from project_tasks - a plain project_tasks token gets 403 on
  PATCH. The seeded internal lead (@taOS-dev) carries it by default so it can
  edit its own board's cards; assignee_id and parent_task_id stay human-only.
- **canvas_read**: `GET .../canvas/elements`, `.../canvas/watch-projection`,
  `.../canvas/snapshot.png|.tldr`, `.../canvas/stream`. **canvas_write**: `POST .../canvas/elements`,
  `PATCH|DELETE .../canvas/elements/{id}`.
- **files_read**: `GET /api/projects/{slug}/files` (list), `.../files/watch`,
  `GET .../files/{path}` (download), `.../trash`, `.../stats`. **files_write**:
  `POST .../files/upload` (multipart), `POST .../mkdir`, `DELETE .../files/{path}`,
  and the trash restore/purge/empty routes. NOTE: the files routes key on the
  project SLUG in the path, not the id.
- **decisions_write**: `POST /api/decisions` (raise a human-in-the-loop
  decision). Listing/answering decisions stays session-only.
- **observatory_control**: read/write the Observatory fleet dials
  (`/api/observatory/pause|throttle|approval-mode|fleet`). Writes require a
  global (null-project) grant; reads admit any active grant. Admin session and
  local token are always allowed.
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

Reserved name prefixes: registration rejects any name whose slug is or starts
with `user-`, `human-`, `admin-` or `taos-` (including casing, spacing and
punctuation obfuscations like `U s e r`), so an external agent cannot mint an
identity that reads as a person or as an internal taOS agent. The public
register route returns 422. The admin-only internal mint/seed path is exempt -
internal driver agents (`taos-dev`, ...) legitimately live under `taos-`.

## Device bearer self-service (second, narrower passthrough)

Beyond the `EXEMPT_PATHS` entry for `GET /api/share/destinations`, a paired
device may call a small fixed set of routes with its scoped bearer. This is a
**different and narrower mechanism**: the path is not exempt from auth, the
middleware simply lets the request through with `user_id=None` so the route's
own `current_user_or_device` dependency resolves the device and synthesizes a
NON-admin identity.

Two properties hold this together and both are enforced in code and tests:

- The passthrough matches only tokens carrying the device prefix
  (`taosdev_`). Matching any bearer previously shadowed valid sessions: a
  logged-in user who happened to send an unrelated `Authorization` header got
  401 on every one of these routes.
- The allowlist is method-and-path anchored. `GET /api/devices`,
  `DELETE /api/devices/{id}` and `POST /api/decisions` are deliberately NOT on
  it and stay session-only.

Device identity always comes from the verified bearer, never from the path or
body, and a device is never admin.

Note for reviewers: answering a decision on this path can apply app, execution
and delegation grants, so a device bearer carries real authority for its own
user. Device scoped tokens do not expire and cannot be self-rotated; the only
revocation is `DELETE /api/devices/{id}` from a session.

## Share destinations (device bearer)

`GET /api/share/destinations` lets a paired device DISCOVER share targets. It is
discovery-only: the response enumerates destinations, but the device scoped
token itself cannot write to the ingest endpoints behind them (library ingest,
chat messages, and project-files uploads all require their own session or agent
auth). Sharing a payload happens through the device share flow, not by the
device calling those endpoints directly.

**Auth model.** `require_device` only: the caller sends
`Authorization: Bearer <scoped_token>` (issued at `POST /api/devices/register`).
Browser sessions and agent JWTs are not accepted. The path is listed in
`EXEMPT_PATHS` in `tinyagentos/auth_middleware.py` so the session cookie gate
does not apply; the handler enforces the device token. CSRF is registered on
the router (`dependencies=_csrf`) so future unsafe-method routes inherit the
double-submit check; the GET is exempt because safe methods always are.

**Coverage.** `agent_chat` destinations resolve through the agent registry
(exact canonical_id, then a slug lookup bounded to the canonical
`-YYYYMMDD-HHMMSS` tail). Only registry-backed agents appear; a plain deployed
agent with no registry row resolves nothing and its DM is omitted.

**Response shape.**

```json
{
  "destinations": [
    {"kind": "library",       "id": "library",        "label": "Library"},
    {"kind": "project_files", "id": "<project-slug>", "label": "<project name>"},
    {"kind": "agent_chat",    "id": "<agent-slug>",   "label": "<display name>"}
  ]
}
```

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

## User resource sharing (share routes)

Users can share resources with each other through the `/api/shares` endpoints
in `tinyagentos/routes/user_shares.py`. The consent loop mirrors the
external-agent consent pattern (`agent_auth_requests.py`): on share-create, a
notification and a Decision record (type `approve_deny`) are raised to the
target user so the desktop consent actions can approve or deny:

- `POST /api/shares {resource_type, resource_id, to_username, permission}` —
  share a resource with another user by username. Resolves the target via
  AuthManager; self-share is rejected (400). Duplicate shares (same owner,
  resource, target, permission) are idempotent.
- `GET /api/shares?direction=out|in` — list shares. `out` (default) returns
  shares the user owns; `in` returns shares where the user is the target.
- `POST /api/shares/{id}/accept` — accept a pending share (target user only).
  Once accepted, the module-level helper `user_can_access()` returns True for
  that resource.
- `POST /api/shares/{id}/deny` — deny a pending share (target user only).
  The share row is preserved with `status=denied` for audit.
- `DELETE /api/shares/{id}` — revoke a share. Owner or admin only
  (requires `require_owner_or_admin` against the share's `owner_user_id`).

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
