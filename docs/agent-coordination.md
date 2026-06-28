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

- `docs/STATUS.md` is the single source of truth for cross-contributor handoff:
  update it when you merge something that others need to know about.
- If your change makes an existing doc inaccurate, fix the doc in the same PR.

These rules are deliberately lightweight. The goal is not process for its own
sake; it is to let many hands move quickly on the same codebase without undoing
each other's work.
