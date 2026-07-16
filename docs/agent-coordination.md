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
`{from, thread, body}` — the destination field is **`thread`**, not `channel`:

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
`from` to the agent's own registry handle (no spoofing) — so it posts as itself,
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
  `GET /api/projects/tasks/{id}/context`. Granting project_tasks also makes the
  agent a project member.
- **canvas_read**: `GET .../canvas/elements`, `.../canvas/snapshot.png|.tldr`,
  `.../canvas/stream`. **canvas_write**: `POST .../canvas/elements`,
  `PATCH|DELETE .../canvas/elements/{id}`.
- **project_doc_review**: `GET .../doc-reviews`, `GET|PUT .../doc-review/{path}`.
- **a2a_send / a2a_receive**: the authenticated bus proxy above
  (`/api/a2a/bus/send|messages|channels|stream`), which forces `from` to the
  agent's own handle.

Access is per-project: a token is authorized for a project only when the agent
holds an active grant + membership there; a request for a project it has no
grant on returns an existence-hiding 404. External agents onboard via the
consent flow (`POST /api/agents/auth-requests`) or a project invite (link +
PIN; see issue #1780). When you change the
allowlist in `tinyagentos/auth_middleware.py`, update this section in the same
PR (the doc-gate enforces it).

These rules are deliberately lightweight. The goal is not process for its own
sake; it is to let many hands move quickly on the same codebase without undoing
each other's work.
