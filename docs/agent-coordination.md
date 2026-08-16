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
- Automated card work uses `exec/<card-id>` for feature work and
  `test/<card-id>` for test-only changes. Every open card lives on its own
  branch cut from `origin/dev`.
- Fetch `origin/dev` and rebase your branch on it before you open the PR, not
  only when you cut it.

## One change per branch, one branch per PR

- Keep each branch and PR to a single logical change. A focused diff reviews
  faster and conflicts less.
- Make surgical edits: touch only what the change requires. Do not reformat or
  refactor adjacent code, and do not edit lockfiles (uv.lock, package-lock,
  etc.) incidentally. If you notice unrelated dead code, mention it rather than
  deleting it in the same PR.
- Every changed line should trace back to the change you set out to make.
- If another open branch already touches a file, leave that file alone. Two
  branches editing one file is the conflict you can still avoid.

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


## Never self-merge

The CI gate merges. Open the PR, let the required checks run, and let the gate
merge when they are green. A PR merged minutes after it opened has not been
reviewed by anything, and an automated review warning on the PR body is a
blocker rather than a note: resolve it before the merge, not after.

## Block instead of guess

If you cannot proceed, post `[BLOCKED] <card-id> <why>` on the coordination
bus. Do not guess, and do not silently work around a blocker.

## Never kill a shared-path process by its path

Every agent on this box runs the SAME scripts out of `~/.taos-team/`, so
`pkill -f a2a_watch` is not a targeted command, it is a fleet-wide weapon. It
matches every other agent's instance identically to your own.

This is not hypothetical. On 2026-08-04 it fired in both directions inside
twelve hours: @taOSmd-dev killed @taOS-dev's watcher twice believing it was a
stray duplicate of their own, and @taOS-dev killed @taOSmd-dev's watcher
believing it was an orphan of theirs. Each of us checked whether the process
matched what we expected OURS to look like, which is a different question from
whose it is. Three watcher deaths and two wrong diagnoses came out of it.

**A kill targeting a shared-path process must be justified by an OWNER check,
never by a path match.** Identity lives in the environment, not the command
line, because the command lines are identical:

    tr '\0' '\n' < /proc/$pid/environ | grep -qx 'A2A_HB_FILE=/home/jay/.my-agent/heartbeat' && kill "$pid"

Three traps in writing that check, all of which have bitten someone here:

- `grep -q "pattern" /proc/<pid>/cmdline` NEVER matches: cmdline is
  NUL-separated. With `&&` it fails closed and reads as a broken reaper; with
  `;` it is decorative, printing a refusal and then killing anyway.
- Substring-matching the JOINED cmdline matches too much: it authorises any
  process that merely MENTIONS the path, including the shell running the check.
- So split on NUL and require an exact element (or an exact env assignment),
  then verify AFTER the signal that whatever you meant to keep alive still
  advances its own liveness file.
- The check and the signal are two operations on a NUMBER, so they race: if the
  process exits between them, Linux can recycle that pid and the `kill` lands on
  something unrelated. Reading `/proc/$pid/environ` first narrows the window, it
  does not close it, and neither does the post-signal liveness check. Where it
  matters, signal through something that owns the process rather than through its
  pid: `systemctl --user kill <unit>` for a unit-owned watcher.

Prefer removing the ambiguity entirely: run your watcher from a uniquely named
copy (`lead_bus_watch.sh`, `taosmd_bus_watch.sh`) so no one else's pattern can
reach it, and write your pid to a pidfile. Under a systemd unit, `systemctl
--user show -p MainPID` is authoritative; a startup-only pidfile can lie between
restarts if a second instance started last.

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

## Reading the bus
Read through the controller with your own registry token, not the raw bus port:
`GET /api/a2a/bus/messages?channel=<name>` with `Authorization: Bearer <your JWT>`.

- `channel=all` (or `*`) reads **every** thread. Use it unless you deliberately want one
  channel. A named channel cannot show you a thread created after you started watching.
- `since` is the cursor and takes a message **ts** (a float), not an id. Passing an id
  reads as a 1970 timestamp and quietly returns everything, every poll.
- Any other query param is a `400`. An unrecognised cursor param is never silently
  ignored, because an ignored cursor is indistinguishable from one that works.
- An empty result for a **named** channel carries `channel_known`. If it is `false`, the
  channel name is wrong; a quiet channel and a typo are otherwise identical.

If the bus is silent, check `channel_known` and your cursor before concluding nobody is
talking. A read that returns `200` with nothing is the failure mode that looks like peace.

## Bus restarts during a controller update

`POST /api/settings/update` on a host that also runs taOSmd locally (config
keys `taosmd_dir` + `taosmd_restart_cmd` set, `memory_url` local) brings
taOSmd to latest in the same action: ff-only pull of the checkout, then a
service restart, then verification against the **running** server's `/health`
(Content-Type must be `application/json` and the core capability identifiers
must appear in the body -- a `text/html` 200 is the SPA catch-all answering and
fails the update loudly). Two consequences for agents:

- A `SYSTEM: taOSmd updating…` message is posted to the `build` thread
  **before** the bus restarts. If your SSE stream drops right after that
  message, it is the update, not an outage -- reconnect and carry on.
- If the hooks are not configured (or `memory_url` is remote) the update
  response reports `taosmd: {"skipped": <why>}`; the skip is visible in the
  response, never silent. On those installs taOSmd is updated on its own host.

An admin session may also call it and set an explicit `from`. On a bus failure
the proxy returns 502 (the read proxies degrade to an empty 200 instead).

## The OS-native agent's identity
Every install mints an identity for the built-in taOS agent at first boot. No admin step, no prompt: if the install has an owner, the agent has an identity.

Before this, the native agent authenticated as the **owner** -- the caller's browser session, or `data/.auth_local_token`, which is admin-equivalent. Its actions were therefore indistinguishable from the human's in every audit trail, it could not appear on the A2A bus as itself, and nothing it did could be revoked without revoking the human.

| | |
|---|---|
| canonical_id | `taos-agent-<install8>-<date>-<time>` |
| handle | `@taOS-agent-<install8>` |
| owner | the install's primary user (`user_id`) |
| scopes | `a2a_send`, `a2a_receive` |
| token | `<data_dir>/.taos_agent_token`, mode 0600 |

Minted by `ensure_native_agent_identity()` in `tinyagentos/native_agent_identity.py`, from two idempotent call sites: `/auth/setup` (fresh install) and lifespan startup (an install that upgraded into this code). Neither is fatal on failure -- an install without the identity is degraded, not broken.

**Anchored to `<data_dir>/.install_id`**, the same id the version ping uses. `install_id()` in `auto_update.py` is public for that reason: two readers of one id, never two ids that can drift apart.

**The handle carries the install discriminator, and must.** The registry holds a unique index on `(handle) WHERE status = 'active'`, so a bare `@taOS-agent` makes the second insert impossible the moment two installs' identities share one registry -- which is exactly what the account/cluster model is for.

**Registry rows carry `install_id`** (migration v6). Blank means **unknown**, not "this install": rows minted before installs were tracked have none, and `list_for_install()` refuses a blank id rather than scooping them all up. That query is what a per-machine revocation would be built on.

**Scopes are deliberately minimal.** Bus participation only. Anything further goes through the normal user-mediated scope-request flow; a first-boot mint that silently granted file or task access would be a privilege grant nobody approved.

**Three boundaries worth knowing before you build on this:**

- The token does **not** authenticate desktop control. `/api/desktop/*` resolves the acting user from a session, and the middleware sets `user_id = None` for registry JWTs, so a registry token arrives there as nobody. Desktop control still uses the session or the host local token.
- **The revocation feed covers agent identities only, and that is a decision rather than a gap.** `GET /api/agents/registry/revoked` reads `agent_registry` and returns `{canonical_id, revoked_at}` per entry. Human credential withdrawal is handled through the session/auth layer, so humans will never appear here. Decided 2026-08-13, after a downstream spec was written assuming the opposite. If you are building something that needs to learn a *human's* credential was withdrawn, this feed is the wrong source and the requirement should be raised rather than implemented against it -- `tests/test_agent_registry.py::test_revoked_feed_shape` fails if the feed is widened, deliberately.
- **Nothing in the chat runtime reads the token yet.** The identity is minted; wiring it into what the agent sends is a separate change. It is deliberately absent from the agent manual until then -- the manual is injected into the agent's prompt and sits at its size ceiling, so it should not describe a capability the agent does not yet have.

## Agent API surface (scoped registry JWT)

A registered external agent authenticates with its registry JWT
(`Authorization: Bearer`) and reaches exactly the routes its granted SCOPES
allow, nothing else: the middleware allowlist is a closed set, no skeleton key.

A SEPARATE credential class exists for the Agent-as-a-Model surface:
`GET /v1/models` and `POST /v1/chat/completions` are reachable without a
session using a CONSENT KEY (`Authorization: Bearer sk-taosagent-...`, minted
by an owner via `/api/agent-model-keys`), which the route itself validates --
no key, no resolution, OpenAI-shaped 401 otherwise. Only those two exact
method+path pairs pass the middleware; any other `/v1` path stays
session-gated. `POST /v1/chat/completions` returns 501 for a valid key until
the opencode host-server turn seam lands (decided 2026-06-23, unbuilt).

The registry-JWT surface, by scope:

- **project_tasks** (the kanban board): `GET /api/projects/{pid}/tasks`,
  `.../tasks/ready`, `.../tasks/{id}`, `.../tasks/{id}/comments` (GET + POST),
  `POST .../tasks/{id}/(claim|release|close|reopen)`, and
  `GET /api/projects/tasks/{id}/context`. This is read + lifecycle + comments
  only. Granting project_tasks also makes the agent a project member.
  `POST .../tasks/{id}/claimable` is also reachable, but LEAD-only: the
  route (`_authorize_project_lead`) refuses a plain project_tasks worker.
  It adds/removes the `claimable` label in place, preserving all other labels,
  so it does not widen the scope into free field edits (cf. PATCH).
  `POST .../tasks/{id}/unquarantine` is also reachable, but LEAD-only: the
  route (`_authorize_project_lead`) refuses a plain project_tasks worker.
  It returns a quarantined card to the open pool and clears its strikes.
- **project_tasks_create**: `POST /api/projects/{pid}/tasks` (author new cards).
  This is a SEPARATE scope from project_tasks and is off by default; grant it
  explicitly when an agent needs to create cards.
- **project_tasks_update**: `PATCH /api/projects/{pid}/tasks/{tid}` on the
  whitelisted fields (title, body, labels, priority), own-or-lead cards only.
  Also SEPARATE from project_tasks - a plain project_tasks token gets 403 on
  PATCH. The seeded internal lead (@taOS-dev) carries it by default so it can
  edit its own board's cards; assignee_id and parent_task_id stay human-only.
- **project_doc_review**: read and write doc-review stamps for a project.
  `GET /api/projects/{pid}/doc-reviews` (list), `GET /api/projects/{pid}/doc-review/{path}`
  (read one), and `PUT /api/projects/{pid}/doc-review/{path}` (set state).
  The route verifies the JWT + grant + project binding; the middleware allowlist
  is closed to these paths only.
- **project_notes**: read and write a project's persistent idea notes
  (title + markdown body). `GET /api/projects/{pid}/notes` (list),
  `POST /api/projects/{pid}/notes` (create), `PATCH /api/projects/{pid}/notes/{nid}`
  (edit) and `DELETE /api/projects/{pid}/notes/{nid}`. One scope covers read and
  write, mirroring project_doc_review. The route verifies the JWT + grant +
  project binding, and a token bound to a DIFFERENT project gets a 404 rather
  than a 403, so it cannot confirm that another project exists. The note's
  author is taken from the verified token, never from the request body.
- **canvas_read**: `GET .../canvas/elements`, `.../canvas/watch-projection`,
  `.../canvas/snapshot.png|.tldr`, `.../canvas/stream`. **canvas_write**: `POST .../canvas/elements`,
  `PATCH|DELETE .../canvas/elements/{id}`.
- **files_read**: `GET /api/projects/{slug}/files` (list), `.../files/watch`,
  `GET .../files/{path}` (download), `.../trash`, `.../stats`. **files_write**:
  `POST .../files/upload` (multipart), `POST .../mkdir`, `DELETE .../files/{path}`,
  and the trash restore/purge/empty routes. NOTE: the files routes key on the
  project SLUG in the path, not the id.
- **decisions_write**: `POST /api/decisions` (raise a human-in-the-loop
  decision), `POST /api/decisions/{id}/answer/agent` (mirror an answer),
  `GET /api/decisions/{id}/agent` (read its own), `GET /api/decisions/agent`
  (list its own). The GENERAL routes stay session-only -- `GET /api/decisions`,
  `GET /api/decisions/{id}`, `GET /api/decisions/{id}/history` and
  `POST /api/decisions/{id}/answer`. The agent set is a separate, narrower
  allowlist distinguished by the `/agent` suffix
  (`_is_agent_decisions_path` in `tinyagentos/auth_middleware.py`).
- **observatory_control**: the Observatory fleet dials.
  `GET|POST /api/observatory/pause`, `GET|POST /api/observatory/throttle`,
  `GET|POST /api/observatory/approval-mode`, and `GET /api/observatory/fleet`
  (read-only, there is no POST). Writes require a global (null-project) grant;
  reads admit any active grant. Admin session and local token are always
  allowed.
- **a2a_receive**: the bus READ routes only -- `GET /api/a2a/bus/channels`,
  `GET /api/a2a/bus/messages`, `GET /api/a2a/bus/stream`.
  **a2a_send**: `POST /api/a2a/bus/send` only, which forces `from` to the
  agent's own handle. These are two separate allowlists in
  `tinyagentos/auth_middleware.py`: an `a2a_receive` token cannot post, and an
  `a2a_send` token is not thereby a reader. Do not describe them as one scope
  covering four routes.

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

Deferred binding and an existing active handle are mutually exclusive. Approving
an auth-request with `defer_binding` mints the token and grants UNBOUND, so the
agent has no project until `assign-agent` binds it. If that agent ALREADY holds
an active handle the approve returns **409** and names
`POST /api/projects/{project_id}/members/assign-agent` as the route to use. Do
not resolve that 409 by minting a second identity: canonical ids are issued once
per agent (`{slug}-{YYYYMMDD}-{HHMMSS}`), and a duplicate splits the agent's
memory and grants across two ids that never reconcile.

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

All owner-gated registry routes are existence-hiding (#2106): an authenticated
caller who is not the owner gets the same 404 body as a nonexistent
`canonical_id`, on the scope-request create/approve/deny routes above and on
registry PATCH, DELETE (revoke), rotate-tokens, and `PUT /api/agents/{id}/org`.
Agents must not treat a 404 from these routes as proof an id does not exist,
and must not expect a 403 to distinguish "exists, not yours". Admin-only
lifecycle routes (approve/reject/suspend/reactivate) still 403 non-admins
before any lookup, which discloses nothing.

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

- `POST /api/shares {resource_type, resource_id, to_username, permission}` --
  share a resource with another user by username. Resolves the target via
  AuthManager; self-share is rejected (400). Duplicate shares (same owner,
  resource, target, permission) are idempotent.
- `GET /api/shares?direction=out|in` -- list shares. `out` (default) returns
  shares the user owns; `in` returns shares where the user is the target.
- `POST /api/shares/{id}/accept` -- accept a pending share (target user only).
  Once accepted, the module-level helper `user_can_access()` returns True for
  that resource.
- `POST /api/shares/{id}/deny` -- deny a pending share (target user only).
  The share row is preserved with `status=denied` for audit.
- `DELETE /api/shares/{id}` -- revoke a share. Owner or admin only
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

## Device pair requests (S4e)

Route module `tinyagentos/routes/device_pair_requests.py`:

- `POST /api/devices/pair-requests` creates a pairing request for a device.
- `GET /api/devices/pair-requests/{pair_request_id}` returns its status.

Approval or denial of a pair request is surfaced to the user through the Decisions app;
agents must not grant pairing directly.

## OS change-event stream (`GET /api/os/events`, session-only)

Route module `tinyagentos/routes/os_events.py`. A Server-Sent Events stream of
typed OS-level change events, behind the session cookie: the path is NOT in
`EXEMPT_PATHS`, so `AuthMiddleware` 401s an unauthenticated request before the
handler runs, and no registry scope reaches it -- a scoped agent token cannot
subscribe. `503` while `app.state.event_bus` is still starting.

- `?kinds=a,b,c` -- comma-separated allowlist of event kinds. Omitted, empty, or
  naming no kind at all (`?kinds=`, `?kinds=%20`, `?kinds=,`) means every kind:
  the allowlist is derived first and an empty one means "no filter", because a
  truthy-but-blank parameter otherwise built a set that matched nothing and the
  stream delivered silence. Filtering happens as events enter the per-connection
  buffer, not as they leave it, so an unrequested kind can never occupy a slot
  and evict something the subscriber did ask for.
- Frame shape is `data: {"kind": ..., "id": ..., "ts": ...}` and nothing else.
  **The payload never crosses the wire** -- `id` is the event's trace id, so a
  subscriber learns that something changed and must refetch to learn what.
- A comment frame `:keepalive` is sent every 10 s so proxies do not close an
  idle stream.
- Frames deliberately carry **no SSE `id:` line**. An `id:` is what makes a
  browser send `Last-Event-ID` on reconnect, and this endpoint ignores that
  header: resume is best-effort through the EventBus replay buffer (the last
  32 events per channel, delivered on subscribe).
- At most 256 events are buffered per connection. Past that the OLDEST
  buffered event is dropped and the client is sent
  `{"kind": "events.lagged", "dropped": N}` -- its cue to refetch rather than
  assume it saw everything. This is a CONTROL frame, not a change
  notification: its `id` is null, and the hook delivers it even when the
  caller asked for a narrow `kinds` list (a subscriber to one kind still needs
  to learn it may have missed some of that kind) and never dedupes it, since a
  null id would make every lag frame after the first look already-seen. The relay never blocks, because a blocked relay
  would stall delivery for the rest of the connection while the bus kept
  filling queues nobody drains.

Both the subscriptions and the relay tasks are created INSIDE the response
generator, not in the handler body. An async generator closed without ever
being iterated never runs its body, so a `finally` there can only undo setup
that also happened there; setting up in the handler leaked a subscription per
client that disconnected before the stream started.

The desktop side is `desktop/src/hooks/use-os-events.ts`:
`useOsEvents(kinds, onEvent)` holds one connection, returns `connected` /
`stale`, dedupes by event id, reconnects with exponential backoff, and reopens
the stream when `kinds` changes (the URL is fixed for the life of a
connection, so a widened list needs a new one).

## LoRA Studio routes (session-only, no agent scope)

Route module `tinyagentos/routes/lora_studio.py`. These are OWNER routes: they
sit behind the session cookie plus the CSRF double-submit on writes, and no
registry scope reaches them. A scoped agent token cannot call them at all, the
same posture as `/api/memory`.

- `POST /api/loras/ingest` -- form field `url`, a `civitai.com` / `civitai.red`
  model page. Answers `202` with the pending row and runs the download in a
  background task; `400` for any other host or an unparseable URL.
- `GET /api/loras` -- `{"loras": [...], "count": n}`, newest first. Optional
  `?status=pending|downloading|ready|failed`.
- `GET /api/loras/{id}` -- one row, `404` if unknown.
- `GET /api/loras/{id}/preview/{n}` -- serves stored preview image `n`; paths
  are re-checked against the archive root before the file is served.
- `DELETE /api/loras/{id}` -- removes the row, the safetensors file, and the
  LoRA directory. Refuses with `400` if a stored path resolves outside the
  archive root rather than deleting it.
- `POST /api/loras/{id}/retry` -- re-runs a `failed` ingest. The `failed →
  pending` transition is a single atomic UPDATE, so concurrent retries get one
  `202` and one `409`, never two download jobs in one directory.

Files land under `models_root()/loras/<slug>/`; `GET /api/models` excludes that
subtree, so adapters never appear as loadable models.

Ingest is direct-connection by default, which is all a host outside a blocked
region needs. Civitai answers HTTP 451 to some regions; the config key
`lora_ingest_proxy_url` (empty by default) is passed to this fetcher only, with
`trust_env=False` so an ambient `HTTPS_PROXY` cannot redirect it. When the
direct request is refused that way and no proxy is set, the ingest fails
loudly, records the actionable error on the row, and leaves no file on disk --
it never stores an error page as a `.safetensors`.

A Civitai URL added to the Library takes the same path: `detect_kind` tags it
`url:civitai` and `CivitaiProcessor` runs the identical ingest job, linking the
resulting `lora_id` back onto the library item.

## Config save and restore (`/api/config`, session-only)

Route module `tinyagentos/routes/settings.py`. Owner routes behind the session
cookie plus the CSRF double-submit on writes; no registry scope reaches them.

- `GET /api/config` -- `{"yaml": "<serialised AppConfig>"}`.
- `PUT /api/config` -- body `{"yaml": "..."}`, optional `?validate_only=true` to
  check without saving. Answers `400` with `details` when validation fails.
- `POST /api/restore` -- multipart `file`, restores a backup tarball into the
  data dir. **The path is `/api/restore`, NOT `/api/settings/restore`**, even
  though the handler sits in `routes/settings.py` beside the `/api/settings/*`
  routes.

**Both write paths REBUILD `AppConfig` field by field**, and a field missing
from either rebuild is silently dropped on the next save, wiping whatever the
user had set. This has now happened twice: `archive`, `archived_agents` and
`github_app_id` (#2375) and `lora_ingest_proxy_url` (#2374). Adding a field to
`AppConfig` means adding it at BOTH sites in this module.
`test_save_config_preserves_all_to_dict_keys` compares the whole `to_dict()`
key set against what survives a round trip and fails if one is forgotten.
Never fix such a leak by removing the field from `to_dict()`: `save_config()`
serialises from there, so that makes the setting unpersistable.
## Identity rules

Work as jaylfc on all git and GitHub activity. Do not add AI attribution to
commits, PRs, or issues. Do not use em dashes in any output: use commas, colons,
or "--".
