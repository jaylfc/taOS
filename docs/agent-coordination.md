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

**A malformed JSON body to an auth route is a 400, not a 500.** `request.json()`
happily parses `null`, `[]`, `1` and `"x"` - all valid JSON, none of them an
object - and the `body.get()` that follows then raises `AttributeError`. Six
routes in `tinyagentos/routes/auth.py` shared that shape, and three of them
(`/auth/login`, `/auth/setup`, `/auth/complete`) are in `EXEMPT_PATHS`, so any
unauthenticated caller could reach the 500. They now answer 400 for a body that
is not a JSON object. If you are driving these endpoints, treat a 400 as "your
body was the wrong shape" and stop reading a 500 there as a server fault worth
escalating. When adding a route that reads a JSON body, use the `_json_object()`
helper rather than parsing inline - it follows the module's existing
`(value, error_response)` convention.

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

## Console-only PIN sign-in (third passthrough, narrowest)

A touchscreen device with no keyboard could not sign in at all, so a PIN is an
alternative proof for the **local console only**. Three paths join
`EXEMPT_PATHS` in `tinyagentos/auth_middleware.py`:

- `POST /auth/pin-login` — exempt for the same reason `/auth/login` is: it is
  how a session is obtained, so gating it on a session would be circular.
- `GET /auth/osk.js`, `GET /auth/pin-panel.js` — the on-screen keyboard and PIN
  panel scripts, served to the signed-out login and setup pages.

Exempt does NOT mean unguarded. `/auth/pin-login` refuses any request that is
not from the device's own console (`auth.is_console_origin`: loopback peer AND
no forwarding header) and throttles per user with an escalating delay
(30s / 5m / 15m, never permanent). Off-console it returns **404** — the same
answer as "no PIN is set" — so a remote guesser cannot learn whether a PIN
exists. LAN and remote/relay callers get password auth only.

`POST /auth/pin` and `DELETE /auth/pin` (set and clear a PIN) are deliberately
**absent** from the allowlist: they require a live session and stay gated by the
middleware. Setting a PIN additionally requires the account password; clearing
one does not, so a user who has forgotten the PIN can always disable it.

An agent token reaches none of this — the PIN surface is console-local by
construction and is not part of the scoped registry-JWT surface above.

Scripts are served as files, not inlined: taOS sends `script-src 'self'`, so an
inline `<script>` on an auth page is silently refused by the browser and the
page renders perfectly while doing nothing. `test_script_is_never_inlined`
guards this. Never "fix" a broken auth-page script by adding `unsafe-inline`.

### CSRF and the sign-in routes

`verify_csrf` is attached **router-wide** in `tinyagentos/routes/__init__.py`
(`app.include_router(auth_router, dependencies=_csrf)`), not per route. Reading
`routes/auth.py` alone tells you the opposite, because only `/auth/logout`,
`/auth/lock` and the two `/auth/pin` routes carry a visible decorator — every
other mutating auth route is protected invisibly. Introspect the **built** app,
not the source, when you need to know whether a route is guarded.

The routes that *establish* a credential are exempt by path
(`_CREDENTIAL_PATHS` in `tinyagentos/middleware/csrf.py`): `/auth/login`,
`/auth/pin-login`, `/auth/setup`, `/auth/complete`, `/setup/complete`. They must
work for a browser still holding an **expired** session cookie — that cookie is
sent, so a "no session cookie" exemption stops applying at precisely the moment
sign-in is needed, and the server-rendered form has no JavaScript to attach an
`X-CSRF-Token` header. The result was a 403 that retrying could not clear.

Every exempt path is also in `EXEMPT_PATHS`; a test asserts that containment, so
the exemption list cannot grow past the surface that is reachable without a
credential. Adding a route here is a security decision — anything that acts on
an already-valid session must stay protected.

Note for anyone writing tests against these routes: **`verify_csrf` runs for
real.** It used to be no-op'd by an autouse fixture for every test file whose
path did not contain the substring `test_csrf` — 788 test files, exactly one
inside the carve-out — so a CSRF repro written as an ordinary test passed
against broken code, which is how #2081 stayed hidden. The opt-out is now the
explicit marker `@pytest.mark.csrf_bypass`, and `tests/test_csrf_bypass_debt.py`
asserts nothing uses it; a filename no longer changes behaviour, so a rename
cannot silently re-arm the bypass.

The shared `client` fixture echoes the `csrf_token` cookie into `X-CSRF-Token`
on mutating requests, exactly as `taosFetch` does in the SPA, so tests *satisfy*
the real check rather than switching it off. A hand-built `AsyncClient` needs
`event_hooks=csrf_event_hooks()` (from `tests/taos_test_csrf.py` — its own
module, because `tests/` is not a package and a bare `from conftest import ...`
binds whichever `conftest.py` is first on `sys.path`). If your test 403s, that
means the real caller could not reach the route the way your test does: send the
header, do not add the marker. See "CSRF in tests" in the development skill for
the full rules.

## Proxy cookie isolation (all four proxies, one shared set)

taOS forwards requests to three different kinds of upstream — the taos.my
account service, container-backed userspace apps, and shortcut targets — through
four proxy modules. **None of them may relay a cookie this origin issued.**

| module | upstream |
| --- | --- |
| `routes/account_proxy.py` | taos.my account service |
| `routes/service_proxy.py` | local services |
| `routes/userspace_apps.py` | container app backends |
| `routes/shortcut_proxy.py` | shortcut targets |

The deny-list lives in **`tinyagentos/issued_cookies.py`** as
`TAOS_ISSUED_COOKIES`, and all four import it. Do not restate it locally: each
proxy used to carry its own hand-written copy, and between them those four
copies named **two** of the five cookies taOS issues. The other three —
`csrf_token`, `taos_browser` (an httponly session id bound to a `user_id`) and
`taos_cs` — leaked from all four. `csrf_token` is the sharp one: it is
`httponly=False` on purpose so the SPA can read it, which makes it a readable
origin-wide secret whose only job is proving same-origin, so relaying it hands
an upstream exactly what satisfies `verify_csrf`.

It is a deny-list rather than an allow-list because an allow-list is not
writable here: upstream's cookie names appear nowhere in this repo —
`account_proxy` relays upstream `Set-Cookie` verbatim (`_rewrite_set_cookie`)
and never enumerates one. What *this* origin issues is knowable and finite.

**Adding a cookie anywhere in the package means adding it to
`TAOS_ISSUED_COOKIES`.** Two guards in `tests/test_proxy_cookie_isolation.py`
enforce this: one asserts all four proxies share the set by **identity**, so
re-forking a private copy fails even if the names match; the other scans every
`set_cookie` call in the package and fails if a cookie is issued but missing
from the set. That second check is the one whose absence caused the bug — all
three leaked cookies were added long after the strip lists were written, and
nothing forced anyone back.

When testing a proxy, the client must hold the cookies whose leak you are
guarding against, and the assertion must name a genuine upstream cookie that
*survives*: a test asserting only "nothing leaked" is satisfied by dropping the
Cookie header wholesale, which would break every proxied login.

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
as the consent flow. The project-bound scopes -- `project_tasks`,
`project_tasks_create`, `project_tasks_update`, `project_lists`, `project_notes`,
the canvas scopes (`canvas_read`, `canvas_write`) and the files scopes
(`files_read`, `files_write`) -- all require an explicit, operator-validated
`project_id` on approval (see `_PROJECT_SCOPES` in
`tinyagentos/routes/agent_auth_requests.py`). Omitting the project picker for
one of these scopes is rejected with 400; the only way to mint a project-bound
grant unbound (`project_id=None`) is the explicit `defer_binding` opt-in, and
such a grant is inert until bound: `check_agent_scope_for_project` only
authorizes a grant whose `project_id` equals the requested project, and the
project-bound routes take their `project_id` from the URL, so an unbound grant
matches nothing and authorizes nothing until assign-agent later binds it.
`project_notes` joined this set in the beta.47 promote (#2320): it was
previously grantable without a `project_id`, which minted an inert note grant
the operator believed was usable; it now follows the same rule as
`project_tasks`. `decisions_read` /
`decisions_write` (and the other global scopes) may be granted globally
(`project_id=None`) or per-project. Creation
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
  Returns `409 Conflict` when no instance admin exists (the request can never be
  approved). The pending cap is enforced atomically so concurrent requests cannot
  exceed it.
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

## What `GET /api/decisions/agent` returns (grant scoping)

Route module `tinyagentos/routes/decisions.py`, scope `decisions_write`. Lists
the decisions THIS agent raised; the store layer enforces the `from_agent`
binding, so there is no cross-agent leakage regardless of grants.

Which of its own decisions come back depends on the shape of the grant:

| grant | returns |
|---|---|
| global (null-project) | **null-project decisions ONLY** |
| exactly one project | that project's decisions, filtered in the store query |
| two or more projects | fetched by agent, then filtered in Python |

**A global grant does NOT mean "see everything".** It means null-project
decisions, matching the rule `_resolve_decision_actor` already applies when the
agent POSTS a decision: an agent with a global grant raises null-project
decisions, so that is what it reads back. Anything relying on a global grant
returning every project's decisions is relying on the older, wider behaviour.

**The `limit` interacts with scoping and only two of the three paths are safe.**
The global and single-project paths push the project filter into the store query,
so the 500 limit applies AFTER scoping (issue #2194). The **two-or-more-project
path still fetches up to 500 rows for the agent and filters afterwards in
Python**, so an agent holding grants on several projects and carrying more than
500 decisions in total can still lose allowed-project rows to the limit. Same
shape as the original bug, narrower blast radius.

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
## Agent memory mode (deploy + `PATCH /api/agents/{slug}/memory`, session-only)

Route module `tinyagentos/routes/agents.py`. Owner routes behind the session
cookie; no registry scope reaches them.

Every agent carries a `memory_mode` alongside its `memory_plugin`, deciding which
memory systems the framework runtime is told to use:

| value | meaning |
|---|---|
| `both` | framework-native memory AND taOSmd (the default) |
| `framework` | the framework's own memory only |
| `taosmd` | taOSmd only |

- **`framework` is ADVISORY today, not enforced.** The mode tells the agent
  runtime what to use; it does **not** yet stop the controller from involving
  taOSmd. A `framework`-mode deploy still registers the agent with taOSmd
  (`routes/agents.py`) and still splices taOSmd rules into `AGENTS.md`
  (`deployer.py`, gated on the agent FRAMEWORK, not on this field). So a taOSmd
  outage can still block a `framework` deploy, and the agent still receives
  taOSmd rules. **Do not choose `framework` expecting isolation from taOSmd.**
  Tracked as `tsk-6tfpun`; this note comes out when the mode is enforced.
- `POST /api/agents/deploy` takes `memory_mode` on the body, defaulting to
  `both`. It is persisted on the agent record and **injected into the agent's
  environment as `TAOS_MEMORY_MODE`** at deploy time, so the runtime honours it
  without a second push. **Deploy validates the pair before any side effect:**
  an unknown `memory_mode` or `memory_plugin` answers `400` naming the valid
  set, and so does a contradictory pair such as
  `{"memory_plugin": "none", "memory_mode": "taosmd"}`, which asks for taOSmd-only
  memory with the taOSmd plugin switched off. No agent is created on rejection.
- `PATCH /api/agents/{slug}/memory` takes `{memory_plugin, memory_mode?}`.
  `memory_plugin` must be one of `taosmd` or `none`; `memory_mode` must be one of
  `both`, `framework` or `taosmd`. Either invalid value answers `400` naming the
  valid set; an unknown slug answers `404`. **Deploy and PATCH share one
  validator**, so a body rejected on one route is rejected on the other.
- **`memory_mode` is OPTIONAL on the PATCH and omitting it leaves the stored
  value alone.** Only `memory_plugin` is required, so a caller that wants to
  change the plugin without disturbing the mode simply leaves it out.
- Agents deployed before this field existed are backfilled to `both` by
  `config.py` when the config loads, so an older agent record without the key
  reads as the default rather than as empty.
## Cluster node revoke, block and unblock (admin-only)

Route module `tinyagentos/routes/cluster.py`. **Admin session only**
(`_require_admin`); no registry scope reaches these, so an agent token cannot
revoke a node. These are the node analogue of the device bearer revoke/block
routes documented above.

- `POST /api/cluster/workers/{name}/revoke` -- kills the node's HMAC signing key,
  so subsequent register and heartbeat requests are rejected. The node **may
  re-pair** through the normal announce/confirm/claim flow to obtain a fresh key.
  Answers `{"revoked": true, "changed": <bool>}`.
- `POST /api/cluster/workers/{name}/block` -- revokes the key AND refuses
  re-pairing until an admin unblocks. **The distinction from revoke is the gate
  it acts at**: a blocked node is turned away at the PAIRING gate, not merely at
  the auth gate, so it cannot come back on its own.
- `POST /api/cluster/workers/{name}/unblock` -- clears the blocked flag only.
  **The old signing key stays dead**, so the node still has to re-pair for a
  fresh one. Unblock is permission to return, not restoration of access.

Behaviour common to all three:

- `404` when the node is absent from the PAIRING store, meaning it was never
  paired. A node that is in the worker registry but has never paired answers
  `404` here.
- `503` when the pairing store is unavailable, kept distinct from `404` so a
  missing subsystem is never reported as a missing node.
- revoke and block mark the in-memory worker **offline immediately** so the
  scheduler stops routing tasks to it, rather than waiting out the heartbeat
  timeout. The worker stays REGISTERED and therefore still visible in
  `GET /api/cluster/workers`, which is what makes it unblockable from the UI.

**Blocked devices keep consuming a per-user slot.** `list_for_user` returns rows
where `revoked=0 OR blocked=1`, so a blocked device counts against
`_MAX_DEVICES_PER_USER` until it is unblocked, at which point the row falls out
and the slot frees. Deliberate: a blocked device is a retained safety valve the
owner can still see and act on.
## Controller generation echo (split-brain protection)

Route module `tinyagentos/routes/cluster.py`, manager logic in
`tinyagentos/cluster/manager.py`. Every controller instance carries a
`generation` identifier; a superseded instance is *fenced* and rejects all
registrations and heartbeats.

- `POST /api/cluster/workers` (register) and `POST /api/cluster/heartbeat`
  both **echo the controller's current generation** in their response
  (`"generation"` key alongside the existing `status` field), so a worker
  always knows which controller instance accepted it.
- A worker sends that generation back on subsequent requests. A request
  carrying a generation that does not match the controller's current one is
  rejected -- registration answers `409` with `{"error": "stale_generation"}`
  (or `"fenced"`), heartbeat answers `404` -- because it means the worker is
  talking to (or was adopted by) **another active controller**. Each rejection
  logs a warning naming the worker and both generations.
- **Legacy workers that send no generation pass** (`None` is accepted) for
  backward compatibility, so the protection only binds once both sides speak
  the protocol.
- The worker's generation-capture guards **log a warning instead of passing
  silently** when the controller stops echoing generation; a silent pass
  would disarm this protection permanently without anyone seeing it.

## Answering a select decision with free text (`other_value`)

Route module `tinyagentos/routes/decisions.py`. Applies to BOTH answer paths:
the human `POST /api/decisions/{id}/answer` and the agent mirror
`POST /api/decisions/{id}/answer/agent` (scope `decisions_write`).

A `single_select` or `multi_select` decision can be answered off-menu by sending
`other_value` instead of, or alongside, `value`:

- `single_select`: send `other_value` and leave `value` empty. Sending both is a
  `400` ("cannot combine value with other_value"). The stored answer is the
  stripped `other_value`.
- `multi_select`: `value` must still be a list and **every element is still
  validated against the declared options**; the free-text entry is appended, so
  the stored answer is `[*declared_values, other_value.strip()]`. A non-list
  `value` is a `400`.
- `note` is a separate optional field. When present it is appended to the text
  routed to the agent as `<answer> (note: <note>)`.
- With no `other_value`, the original strict validation is unchanged: the answer
  must be one of, or a subset of, the declared options, and a non-hashable or
  non-iterable value fails closed as `400` rather than `500`.

**Two consequences worth knowing before you build on this.**

- **There is no per-decision opt-out.** No `allow_other` flag exists, so the
  free-text path is available on EVERY select decision. A decision author cannot
  declare a closed option set and have it enforced. Before this, the route
  comment asserted that "the answer must reference the declared options so a
  stale or malformed client cannot record an arbitrary value"; that invariant now
  holds only for callers who do not send `other_value`.
- **The agent path gained it too.** An agent holding `decisions_write` can record
  arbitrary free text where it was previously constrained to the declared
  options. `source` is still derived server-side and cannot be spoofed, so the
  audit trail still distinguishes `in_app` from `mirrored_from_chat`, but the
  VALUE is no longer bounded by the option list.

## Identity rules

Work as jaylfc on all git and GitHub activity. Do not add AI attribution to
commits, PRs, or issues. Do not use em dashes in any output: use commas, colons,
or "--".

## Agent-token API surface (Bearer allowlist)

The auth middleware keeps an explicit allowlist of routes a registry JWT
(agent Bearer token) may reach; everything else on `/api` requires a user
session. When you change the allowlist in `tinyagentos/auth_middleware.py`,
record the change here so the agent-facing surface stays reviewable in one
place.

Task checklist items (added with the OS-owned objective checklist, #2415):

- `GET /api/projects/{project_id}/tasks/{task_id}/checklist-items` -- list;
  Bearer-reachable so the handler's `project_tasks_create` scope check runs
  instead of the middleware refusing 401 at the gate.
- `POST /api/projects/{project_id}/tasks/{task_id}/checklist-items` -- create;
  same scope check.
- `DELETE` and per-item subpaths (`.../checklist-items/{item_id}`) stay
  session-only: no agent-reachable handler exists, and the allowlist must not
  widen past list + create.
