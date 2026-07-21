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
For project-scoped routes, copy the house guard verbatim:
`if not user.is_admin and user.user_id != p["user_id"]` followed by a masked
404 (see routes/projects.py). A bare 403 without the admin bypass both locks
out admins and leaks resource existence to other users (#2042).

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

**16. Never commit runtime state or key material.**
Anything a store or subsystem writes under `data/` at runtime is state, not
source. A PR that introduces a component writing under `data/` must add that
directory to `.gitignore` in the SAME PR, and `git status` must be checked for
runtime artifacts before every commit. Any private key that reaches a pushed
commit is burned: regenerate it, and keep it out of the target branch's history
(squash merge, or rewrite the branch). Example: `data/hub/identity.json` with
live signing/encryption keys entered one branch's history and was then
re-committed by a second PR (#2043 history, #2042).

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

**17. A new view must be wired into every surface it has: desktop AND mobile.**
The desktop tab list and the mobile tab order are separate registries; updating
one and not the other ships a view that is unreachable on phones (#2042:
`TABS` updated, `mobileTabOrder` missed). Grep for every registry the sibling
views appear in and update all of them.

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

**18. Retrofitting a column onto an already-shipped store needs a guarded
ALTER, not just a MIGRATIONS entry.**
The migration runner baselines pre-existing databases at the latest version
WITHOUT executing the migrations (FOOTGUN #2 in `db_migrations.py`'s own
docstring), so a plain `MIGRATIONS = [(1, "ALTER TABLE ...")]` on a store that
already shipped is a silent no-op on every upgraded install: fresh DBs work,
upgraded DBs lose the feature at runtime. Use the guarded `_post_init` pattern
(PRAGMA table_info, ALTER only when the column is absent - see
`knowledge_store._migration_v1_add_user_id`) and add an upgrade test that
builds the PRE-change schema first. Fresh-DB tests are structurally blind to
this class (#2043: `peer_fingerprint`).

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
A finding is closed only when it is ANSWERED IN-THREAD: reply to each numbered
item with the commit that addresses it or a concrete rebuttal. Pushing code
without replies leaves the finding open - the reviewer re-verifies blind and
the verdict stays HOLD (#2043 round two).

**19. State scope deltas against the issue explicitly.**
If a PR ships less than its issue scopes - a subfeature dropped, owner-only
routes where the issue says peer-serving, a "live" view that fetches once -
say so in the PR body and file the follow-up issue in the same push. Never let
a partial slice close the parent issue. Silent shortfalls read as done, get
caught in review anyway, and cost a full extra round (#2042: community chat
and peer access absent with no deferral note).

**20. A tolerance is not a test of an invariant.**
An assertion like `assert abs(after - before) <= 2` cannot distinguish correct
behaviour from catastrophic failure. In PR #2062 that exact assertion ran green
while reprocess destroyed the user's original uploaded file: the observed values
were `before=2, after=0`, which the tolerance accepted, and the same tolerance
would equally have accepted a doubling to 4. If the property is "the count does
not change", assert equality and assert the resulting status, so the test fails
loudly on both loss and duplication. Reserve tolerances for genuinely
approximate quantities such as timings, and even then bound them tightly.

**21. Shell snippets inside template literals must escape `${`.**
A bash or PowerShell snippet stored in a JavaScript template literal collides
with the language's own interpolation: `${VAR:-default}` is parsed as JS, not
shell. In PR #2077 this produced 225 TypeScript syntax errors from a single
cause, in one of four otherwise-clean files, and read like incoherent output
rather than one mechanical mistake. Escape as `\${`, or keep snippets in plain
non-template strings or separate asset files. The class generalises to any
language sharing `${...}` with the shell, and it is invisible to anything that
does not actually compile the file, which is why the frontend typecheck gate
exists before a PR is opened.

**22. Conflict resolution is a decision, not a mechanical act.**
Taking the wrong side of a hunk silently reverts fixes that were just made. When
a base branch has moved substantially under a long-lived branch, read what
changed underneath before resolving: the four defects fixed in Library P1
(nested response envelope, the guard preventing reprocess from deleting the
source upload, the compare-and-swap status transition, exact artifact-count
assertions) are all reintroducible by a plausible-looking resolution. Rebase
rather than merging the base in, so the diff stays reviewable and each conflict
is seen individually.

**23. Mocks of EXTERNAL service contracts need a real source, not a guess.**
Mocking our own internals or injecting errors you cannot produce on demand (a
500, a timeout, an ImportError) is fine and unavoidable. The dangerous class is
narrow: a mock of a service we do not control, whose fixture was hand-written
from what the calling code expects. That fixture encodes a BELIEF about someone
else's API, and when the belief is wrong the test proves nothing while going
green. This happened three times in one PR cycle (#2062): an invented `dbPath`
request shape, an un-nested poll response, and a tolerance assertion over both.

First, split the class by whether the contract has a reachable OWNER.

**Sibling services (taOSmd, taOS website): ASK. Do not guess.** These are not
third parties. Their maintainer is on the A2A bus, and asking costs one message.
Every failure in the #2062 cycle came from inferring a contract that was free to
obtain: the builder guessed a request shape, and the reviewer verified against
source rather than asking the owner. When we finally asked, we got the envelope
documented, a wrong stats key list corrected by its own author, and a
`/version` capabilities endpoint built to make the contract machine-checkable.
Reverse-engineering a sibling's API from its source is a smell, not diligence:
source tells you what it does today, the owner tells you what it guarantees.
Contributors without bus access (external collaborators) ask in the PR, and the
lead relays.

**Genuinely third-party (GitHub, OpenRouter, Reddit): capture, do not compose.**
There is no one to ask, so call the real service once and commit its response as
the fixture. A recorded response cannot encode a wrong belief.

For both, then:

1. **Keep one real integration test per external contract.** Have it feature
   detect and skip when the service is unreachable, so CI stays green offline
   but drift surfaces the moment anyone runs it against a live instance. For
   taosmd, `GET /version` returns a capabilities list for exactly this.
2. **If that is impossible, mark the mock provisional IN CODE** with the
   contract source and the date it was verified. A follow-up issue is not
   sufficient: it detaches the caveat from the code and, in a tracker with
   hundreds of open items, functions as indefinite deferral.

Reviewer's job: ask where the fixture came from. A green test over a fictional
contract is worse than no test, because it certifies the bug.
