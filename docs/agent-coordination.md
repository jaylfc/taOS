# Agent coordination discipline

One branch per card, one card per PR, no surprises.

## Branch naming

Use `exec/<card-id>` for feature work and `test/<card-id>` for test-only changes. Every open card lives on its own branch from `origin/dev`.

## Work in an isolated worktree

When you pick up a card, create a dedicated worktree so parallel agents never collide on the index or working tree:

    git fetch origin
    git worktree add ../taos-<card-id> -b exec/<card-id> origin/dev

Remove the worktree once the branch merges. Never share or reuse another agent's worktree.

## Rebase before you open the PR

Fetch `origin/dev` and rebase your branch on it before opening the PR. Stale bases are the most common source of avoidable merge conflicts.

## One card, one conflict-free PR

Edit only the files your card touches. If another open card touches the same file, do not touch it. If a PR grows beyond one logical change, split it.

## Never self-merge

The CI gate merges. Open the PR, let required checks run, and let the gate merge when they are green.

## Block instead of guess

If you cannot proceed, post `[BLOCKED] <card-id> <why>` on the coordination bus. Do not guess or silently work around a blocker.

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

## Identity rules

Work as jaylfc. Do not add AI attribution to commits, PRs, or issues. Do not use em dashes in any output.
