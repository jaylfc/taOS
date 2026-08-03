# Agent Coordination Discipline

Multi-agent build lane rules. Follow them exactly.

## One branch per card

- `exec/<id>` for implementation, `test/<x>` for test-only work.
- One card maps to one branch, one PR, one merge.

## Isolated worktree per card

- Check out each card in its own worktree. Never share a working copy.
- Rebase on `origin/dev` before opening the PR.

## Do not touch another card's files

- If another open card touches a file, leave it alone.
- If you cannot complete your card without stepping on someone else's work, post `[BLOCKED] <id> <why>` and stop.

## One card, one conflict-free PR

- Keep the diff small and focused. One logical change per PR.
- Never self-merge. The gate merges.

## Identity and attribution

- Author is jaylfc. No AI attribution, no Co-Authored-By lines.
- No em dashes in any output, code, or comments. Use commas or "--" instead.
