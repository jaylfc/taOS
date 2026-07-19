# Recurring review pitfalls

Patterns that keep coming back in review on this repo. Every one of these has
blocked at least one real PR. Check your diff against this list before pushing;
reviewers gate on these without exception.

## Security and auth

**1. Every new endpoint needs an explicit auth gate and a negative test.**
A new route is unauthenticated until proven otherwise. Add the guard (admin
session + CSRF, or the agent-token allowlist in `auth_middleware.py`) and a
test asserting an unauthenticated or cross-principal caller gets 401/403.
Example: PR #2036 added `GET /api/secrets/agent/{name}/github` with no guard,
leaking installation IDs and repo names to any caller.

**2. Bind both directions on authenticated channels.**
When a message or envelope arrives over an authenticated channel, verify BOTH
that `from` matches the authenticated peer (sender binding, blocks
impersonation) and that `to` matches the local identity (audience binding,
blocks relay abuse). Compare against the correct side: the audience check
compares `to` with OUR identity, not the sender's. Example: PR #2025 shipped
the audience check reversed (`to == contact_id` where contact_id was the
sender), which 403'd legitimate traffic and allowed signing as another
identity; PR #2034 forwarded an in-body `recipient` with no binding at all.

**3. Fail-closed stays fail-closed.**
If a change makes missing secrets or config reject requests, never weaken that
behavior to make tests pass. Provision the secret in the test environment (CI
job `env:` or fixtures) instead. Example: the taos-website hardening stack
returned 400 on all 153 tests because CI set no secret; the fix is test env
provisioning, not removing the gate.

**4. Secrets live in SecretsStore, never in config files.**
Anything sensitive (private keys, tokens) goes through SecretsStore encrypted
at rest, with a migration that moves any legacy plaintext value out of config
and strips it. Tokens we only VERIFY are stored hashed; tokens we must PRESENT
outbound are the only ones stored recoverable. Example: PR #2009 moved the
GitHub App RSA key out of plaintext config.yaml.

## Correctness

**5. Never take element `[0]` of a collection that can hold more than one.**
If a lookup returns a list (installations, grants, devices), resolve the RIGHT
element for the context or handle all of them. Index 0 is an arbitrary choice
that becomes a silent wrong answer the day a second element exists. This has
now recurred twice: PR #1997 (installations dict snapshot) and PR #2036
(`get_agent_github_token` using `installations[0]`).

**6. Measure limits on the encoding you enforce them against.**
A size cap documented "on the wire" must measure the wire form (base64 inflates
about 33 percent). Pick one authoritative form, measure that, and make the
docstring match. Example: PR #2034 measured raw JSON while promising a base64
wire limit.

**7. Store timestamps in UTC.**
Server-local timestamps corrupt data the moment the host timezone differs.
Convert at the edge, store UTC. Note `datetime.fromisoformat()` only accepts a
trailing `Z` on Python 3.11+; use `+00:00` in examples and normalize input.
Examples: PR #1944 (due/remind stored server-local), PR #1935 (doc example).

**8. No empty catch blocks.**
Swallowing a fetch or IO failure and rendering an empty state reads as "no
data" when the truth is "request failed". Surface the error (toast, inline
state, log). Example: PR #2037 `loadLists` swallowed all fetch failures.

**9. Refactors must preserve behavior, or say loudly that they do not.**
When extracting or splitting a component, diff the capability list before and
after. If something is intentionally dropped (a panel, revision history, a
share flow), state it in the PR body and file a follow-up issue. Example: PR
#2037 silently lost the Share/members panel and per-entry revision history.

**10. No hardcoded placeholder arguments.**
`permissions: []` hardcoded at a call site means every save writes empty
permissions. Wire the real value or do not add the parameter yet. Example:
PR #2036 `handleSaveGrants`.

## Store and schema

**11. SCHEMA is the frozen v1; new columns and their indexes go in MIGRATIONS.**
Never index a migration-added column inside SCHEMA: fresh installs work but
every EXISTING database bricks on boot (SCHEMA runs before MIGRATIONS).
`scripts/check_schema_migrations.py` guards this; run it locally. Always test
upgrades against a pre-change database, not just a fresh one.

**12. Migrations are idempotent.**
Running a data migration twice must be a no-op (existence checks, INSERT OR
IGNORE, migrated-from markers) and there must be a test proving the second run
changes nothing. Example done right: PR #2028 `test_migrate_idempotent`.

## Process

**13. A fix and its test belong in one PR.**
Splitting a behavior change and the test update that proves it into separate
PRs means neither can go green alone and reviewers cannot evaluate either.
Example: the 500 to 422 store-signing change fragmented across #2023, #2026,
and #2027 before being consolidated into #2023.

**14. Fork PRs: make sure real CI actually ran.**
Bot reviews (Kilo, CodeRabbit) are not CI. If `test (3.12/3.13)`, `lint`, and
`spa-build` are absent from the checks list, the workflow never fired; rebase
or push an empty commit to trigger it. A PR is mergeable only on genuine green
from those jobs at the CURRENT head, against the CURRENT base: a green computed
before the base branch moved is stale, because two individually green PRs can
conflict semantically with no textual conflict (see the #2009/#1932 App-key
incident, fixed in #2041). If the base has advanced since the last CI run,
rebase or re-run before merging.

**15. Fold every review finding before merge.**
Findings are gated on severity of content, not review state. Address each one
(fix it or rebut it concretely in the thread); never merge past an open
finding, and never let a "pass" verdict from a stale commit stand in for the
current head.
