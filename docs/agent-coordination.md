# Agent Coordination

How the multi-agent build lane works without stepping on toes.

## Worktree and Branch Layout

Each agent card gets its own git worktree and branch:

- **exec cards**: `exec/<id>` (feature work, implementation)
- **test cards**: `test/<x>` (test-only changes)

One card = one branch = one worktree. Never reuse a branch across cards.

## One Branch Per Card

- Create a new branch off `origin/dev` for every card.
- The branch lives only as long as the card is open.
- When the PR merges, the branch and worktree are deleted.

## File Ownership

- Never edit a file that another open card touches.
- If two cards need the same file, serialize: one merges first, the other rebases.
- Before starting work, check open branches (`git branch -r | grep -E "^  exec/|^  test/"`) and avoid overlap.

## Keep Isolated

- Work in your assigned worktree only. Never jump into another agent's tree.
- All edits, builds, and tests happen inside the worktree.
- The shared checkout is for reading only.

## Rebase Before PR

Before opening a PR:

```
git fetch origin
git rebase origin/dev
```

Fix any conflicts in your worktree. A PR with conflicts will not merge.

## One Card, One Conflict-Free PR

- Each card produces exactly one PR.
- The PR must be rebase-clean on `origin/dev` at time of merge.
- Small scope. If a card balloons, split it.

## No Self-Merge

- Agents never merge their own PRs.
- The gate (CI + review) merges. Post your PR and wait.
- If the gate rejects, fix in your worktree and update the PR.

## Blocked Cards

If you cannot finish a card, post in the card thread:

```
[BLOCKED] <id> <why>
```

Do not guess, do not hack around it, do not silently skip. State the blocker clearly and move on.

## Identity and Commits

- Author is always `jaylfc` / `jaylfc25@gmail.com`.
- No AI attribution in commits, PRs, or any output.
- No `Co-Authored-By` lines.
- No `Generated with` lines.
- Use commas or `--` instead of em dashes.
