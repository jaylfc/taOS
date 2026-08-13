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

## The OS-native agent's identity

Every install mints an identity for the built-in taOS agent at first boot. No admin step, no prompt: if the install has an owner, the agent has an identity.

Before this, the native agent authenticated as the **owner** — the caller's browser session, or `data/.auth_local_token`, which is admin-equivalent. Its actions were therefore indistinguishable from the human's in every audit trail, it could not appear on the A2A bus as itself, and nothing it did could be revoked without revoking the human.

| | |
|---|---|
| canonical_id | `taos-agent-<install8>-<date>-<time>` |
| handle | `@taOS-agent-<install8>` |
| owner | the install's primary user (`user_id`) |
| scopes | `a2a_send`, `a2a_receive` |
| token | `<data_dir>/.taos_agent_token`, mode 0600 |

Minted by `ensure_native_agent_identity()` in `tinyagentos/native_agent_identity.py`, from two idempotent call sites: `/auth/setup` (fresh install) and lifespan startup (an install that upgraded into this code). Neither is fatal on failure — an install without the identity is degraded, not broken.

**Anchored to `<data_dir>/.install_id`**, the same id the version ping uses. `install_id()` in `auto_update.py` is public for that reason: two readers of one id, never two ids that can drift apart.

**The handle carries the install discriminator, and must.** The registry holds a unique index on `(handle) WHERE status = 'active'`, so a bare `@taOS-agent` makes the second insert impossible the moment two installs' identities share one registry — which is exactly what the account/cluster model is for.

**Registry rows carry `install_id`** (migration v6). Blank means **unknown**, not "this install": rows minted before installs were tracked have none, and `list_for_install()` refuses a blank id rather than scooping them all up. That query is what a per-machine revocation would be built on.

**Scopes are deliberately minimal.** Bus participation only. Anything further goes through the normal user-mediated scope-request flow; a first-boot mint that silently granted file or task access would be a privilege grant nobody approved.

**Two boundaries worth knowing before you build on this:**

- The token does **not** authenticate desktop control. `/api/desktop/*` resolves the acting user from a session, and the middleware sets `user_id = None` for registry JWTs, so a registry token arrives there as nobody. Desktop control still uses the session or the host local token.
- **Nothing in the chat runtime reads the token yet.** The identity is minted; wiring it into what the agent sends is a separate change. It is deliberately absent from the agent manual until then — the manual is injected into the agent's prompt and sits at its size ceiling, so it should not describe a capability the agent does not yet have.
