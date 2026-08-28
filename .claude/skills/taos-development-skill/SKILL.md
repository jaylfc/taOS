---
name: taos-development-skill
description: taOS architecture map, contribution workflow, testing guide, common fix patterns, and coding conventions. Load when contributing to taOS - PRs, bug fixes, features, catalog additions.
---

# taos-development-skill

Procedures and architecture for contributing to
[taOS](https://github.com/jaylfc/taOS). The non-negotiable rules live in
`soul.md`; this skill is the *how*.

> uses approximate counts (~N) as rough orientation only - actual numbers rot fast in a
> living repo. Trust the tree, not tallies.

## Repository layout

```
<clone>/
  tinyagentos/                 ← server package
    app.py                     ← FastAPI app factory: lifespan, route registration
    config.py                  ← Platform config, hardware detection
    routes/                    ← one APIRouter module per feature area (~127 modules)
    templates/                 ← minimal: agent_debugger.html only (frontend is React SPA)
    channel_hub/               ← framework-agnostic messaging: connectors + MessageRouter
    adapters/                  ← per-framework agent adapters (generic ~20 lines, acp ~500); each declares verification_status
    cluster/                   ← distributed compute: worker registry, task routing, GPU lease
    worker/                    ← cross-platform worker apps (system tray, Android, iOS)
    *_store.py, base_store.py  ← data layer: top-level BaseStore subclasses (aiosqlite); one SQLite file per store; no stores/ dir
    chat/ projects/ mcp/       ← chat, project board/canvas/A2A, MCP proxy+permissions
    installers/ containers/    ← model/app installers; Docker + LXC backends
    migrations/                ← DB migrations
  desktop/                     ← React + TypeScript SPA (Vite)
  app-catalog/                 ← YAML app manifests + catalog.yaml
  tests/                       ← pytest suite (large; counts rot - trust the tree)
  docs/                        ← documentation; agent manual compiled from docs/agent-manual/
```

## Key architectural patterns

- **Routes** - each `routes/*.py` is an `APIRouter` registered in
  `tinyagentos/routes/__init__.py`'s `register_all_routers()` (called from `create_app()`) with
  `dependencies=_csrf`. `async def` handlers, `await` all I/O, Pydantic request/response models.
  Routes access stores via `request.app.state` (dependency injection set up in the app lifespan) -
  they do **not** import stores directly. **No cross-importing between route modules.**
- **Stores** - SQLite via `aiosqlite`; each subclasses `BaseStore` (`tinyagentos/base_store.py`),
  sets `SCHEMA` (first-open DDL) and `MIGRATIONS`, and is attached to `request.app.state` in the
  lifespan (`app.state.secrets`, …). Never reference a migration-added column inside `SCHEMA`.
- **Config** - `AppConfig` dataclass in `config.py`; YAML serialisation; async-locked saves via
  `save_config_locked()`; typed backends (`rkllama`, `ollama`, `openai`, `anthropic`, …).
- **Templates** - all real UI work goes in the `desktop/` React SPA. One legacy Jinja template
  exists (`agent_debugger.html`); if you must touch it, match its existing Pico CSS + htmx style.
- **Frontend** - React + TypeScript SPA in `desktop/`. Built with Vite: `npm run build` outputs
  to `static/desktop/` (gitignored). For development: `npm run dev` serves with hot reload on
  port 5173. One concern per component; API calls in dedicated hooks or service files.
- **Cluster** - worker registration, routing to remote nodes, model archive/promotion on capable
  hardware, GPU lease claim/release, hardware-tier compatibility.

## Git workflow

### Before starting

1. **Sync from upstream.** In a fork clone, `origin` is *your fork* (push target) and `upstream`
   is `jaylfc/taOS` (fetch/rebase target). Add upstream once:
   `git remote add upstream https://github.com/jaylfc/taOS.git`. Always rebase onto the canonical
   branch, never your fork's possibly-stale `dev`:
   ```bash
   git fetch upstream dev
   git checkout dev
   git rebase upstream/dev
   ```
   Never branch from a stale `dev`.

2. **Create an isolated worktree** (recommended for concurrent work):
   ```bash
   git worktree add /path/to/worktree dev
   cd /path/to/worktree
   ```
   When done: `git worktree remove /path/to/worktree` (or `--force` if needed).

3. **Create a branch:** `feat/<slug>` or `fix/<slug>` matching the task.

### After completing the work

4. **Commit before anything else.** Code → test → `git add` → `git commit` (conventional message) →
   push to fork.

5. **Open a draft PR, then mark ready immediately:**
   ```bash
   gh pr create --repo jaylfc/taOS --head <user>:<branch> --base dev --draft \
     --title "feat(scope): description" --body "Fixes #<issue>. Tests: N/N pass."
   gh pr ready <PR#>
   ```
   Fork CI approval is per-contributor: a FIRST-TIME contributor's workflow runs sit in
   `action_required` until a maintainer approves (mark ready once the CODE is done and local tests
   pass, then surface the approval need); a RETURNING contributor's CI runs automatically - no
   approval wait, and you own making it green (`gh pr checks <PR#>`: test 3.12/3.13 + lint +
   spa-build all pass) before considering the task done.

6. **Never commit directly to `dev` or `master`.** All work happens on branches.
   Main only receives merges via upstream PR approval.

### Branch naming

`feat/<slug>` or `fix/<slug>`. Keep it short and descriptive.

### Commit messages

Conventional commits only:

| Prefix | Use for |
|--------|---------|
| `feat:` | new feature |
| `fix:` | bug fix |
| `docs:` | documentation only |
| `refactor:` | code change with no behaviour change |
| `test:` | adding or updating tests |
| `chore:` | tooling, deps, CI |

No AI tool attribution in commit messages.

## Testing

Run **targeted tests first**, then the fast parallel gate. **Never run the un-parallelised full
suite (`pytest tests/ -v`) locally** - it is massive and will take far too long. CI owns the
full 3.12–3.13 matrix (3.11 on nightly cron only).

```bash
# 1. Targeted - the changed module + related tests, always first:
uv run pytest tests/test_<changed_module>.py tests/<related>/ -v

# 2. Canonical local gate - parallel, run before marking ready:
uv run pytest tests/ --ignore=tests/e2e -n auto
```

### Dependency-audit ignore hygiene

`security/pip-audit-ignore.toml` suppresses advisories that have no released
fix. `scripts/check_dependency_audit_ignores.py` (run by
`.github/workflows/security.yml` on PRs and a Monday cron) re-evaluates the
list every run so it cannot rot: it probes `uv lock --upgrade-package` per
ignored package to see whether a fixed version now resolves, and runs
`pip-audit` WITHOUT the ignore flags to catch any finding not on the list.
If it fires: a resolvable fix means take the upgrade and drop the entry; a
new advisory means triage it, never blanket-add it. Entries for tool-level
deps the project does not pin set `check_upgrade = false`.

### Test conventions

- `conftest.py`: `tmp_data_dir` fixture creates temp config + SQLite
- `app` fixture: `create_app(data_dir=tmp_data_dir)`
- `client` fixture: `AsyncClient(transport=ASGITransport(app=app))` - async HTTP test client
- Module mirroring: `tests/test_routes_agents.py` tests `routes/agents.py`
- SPA stubs: conftest creates stub `index.html`/`sw.js` so tests don't need `npm run build`
- E2E (Playwright) tests excluded from CI and local gate

### CSRF in tests - ON by default

`verify_csrf` runs for real in tests. It used to be no-op'd by an autouse fixture for every
file whose path lacked the substring `test_csrf` - measured at 788 test files, exactly ONE
inside that carve-out, so 787 ran against an app whose CSRF dependency did nothing. That is
what hid #2081: a repro written as an ordinary test returned 303 and PASSED, and the tell was
that the CONTROL passed identically, which is the shape you get when the input never reaches
the system under test.

What this means when you write a test:

- **The shared `client` fixture already does the right thing.** It echoes the `csrf_token`
  cookie into `X-CSRF-Token` on mutating requests, exactly as `taosFetch` does in the SPA.
  Nothing to remember.
- **If you build your own `AsyncClient`, it will 403 on mutating routes** once it carries a
  `taos_session` cookie. Fix it the way the real caller does - send the header - not by
  disabling the check. One line does it:

  ```python
  from taos_test_csrf import csrf_event_hooks   # tests/taos_test_csrf.py

  AsyncClient(
      transport=ASGITransport(app=app),
      base_url="http://test",
      cookies={"taos_session": token},
      event_hooks=csrf_event_hooks(),      # <- echoes the cookie into the header
  )
  ```

  It lives in its own module, not in `conftest.py`, because `tests/` is not a package and
  several `conftest.py` files exist, so a bare `from conftest import ...` binds whichever one
  is on `sys.path` first.
- **`verify_csrf` exempts** safe methods, `Authorization: Bearer` callers, `_CREDENTIAL_PATHS`
  (the sign-in surfaces), and any request with no `taos_session` cookie. A test that never
  authenticates is unaffected.
- **`@pytest.mark.csrf_bypass` exists but nothing uses it, and
  `tests/test_csrf_bypass_debt.py` asserts the list stays EMPTY.** Adding a marker turns that
  guard red on purpose. Do NOT add it to silence a new red: a red means a route the real
  caller could not reach the way your test reaches it - give the client the event hook
  instead.
- **A filename no longer changes behaviour.** The old carve-out was a substring match on the
  path, so renaming a file silently re-armed the bypass with no failure anywhere.

Patch timing matters if you ever stub it yourself: `register_all_routers` does
`from ... import verify_csrf` and freezes the object into `Depends(...)` at `include_router`
time, so patching the module attribute AFTER `create_app` does nothing.

### CI matrix

- Python 3.12 + 3.13 on every PR/push; 3.11 on nightly cron only
- GitHub Actions: `.github/workflows/ci.yml` in upstream repo
- Uses `uv sync --frozen` and `pytest -n auto`
- Also required: `spa-build` (npm build + tsc + **vitest** - a desktop type error or failing
  component test fails CI), a "Verify app starts" `create_app` import smoke, `lint`
  (`compileall`), and `cla`. The doc-gate, store-wiring gate, bot-review gate,
  distrust-green gate, and evil-merge gate (`.github/workflows/evil-merge-gate.yml`,
  implementation in `scripts/check_evil_merge.py` — fails a PR whose merge resolution
  invents test-file content differing from the `git merge-tree` baseline; the workflow
  selftests that it fires RED on the known evil merge `ad5cdfb0c` before checking the
  PR) are separate workflows.
- `check-all-skip` (`.github/workflows/distrust-green-gate.yml`, implementation in
  `.github/scripts/check_all_skip.py`) fails a PR when a test file it adds or modifies
  has tests and ALL of them skip (e.g. `pytest.importorskip` on a module that does not
  exist yet) — green CI that asserts nothing. The failure names the file and the guard.
  Landing tests ahead of code stays legal via an explicit waiver trailer in the PR body:
  `Tests-Skipped-Intentionally: <file>, <why>`. Files with SOME skips pass (v1 scope).
- **Gate integrity** (`.github/workflows/gate-integrity.yml`, implementation in
  `scripts/check_gate_integrity.py`): because each `pull_request` gate checks
  out the PR merge ref and runs its checker FROM that checkout, a PR can edit
  its own gate to always-exit-0 and green-pass the check that gates it. This
  workflow runs on `pull_request_target` from the base ref and inspects the PR
  diff via the GitHub API only -- it never checks out or executes PR code. It
  fails any PR touching `.github/workflows/`, `.github/scripts/`,
  `scripts/check_*.py`, `docs/doc-gate.toml`, `pyproject.toml`, or
  `tests/conftest.py` unless the PR carries the human-set
  `gate-integrity-allow` label (the only way to land a legitimate change to CI
  or a gate checker). Add that label yourself; lanes must not add it. Branch
  protection must require "Gate integrity" as a blocking check.
  **Retargeting a PR re-runs this gate, and it can newly fail.** The verdict is
  a function of the base..head diff, so a PR that passed against one base can
  touch protected files against another; the workflow subscribes to the
  `edited` activity type (which is what a base change fires) so the new diff is
  actually inspected. If you change a PR's base and this gate goes red, that is
  the new diff being judged, not a flake -- re-read what the failure names.

## CLA - HUMAN signs

taOS requires a Contributor License Agreement for first-time contributors. The CLA bot
flags the PR with a `cla: fail` check.

**The agent does NOT sign the CLA.** Posting the acceptance text accepts a legal agreement - that is the human account-holder's action, not the agent's.

### Procedure when `cla: fail` appears:

1. Verify the commit author email matches a GitHub-verified email.
   If the email was wrong, amend the commit with the correct email, force-push.
2. Surface the bot's comment + link to the human. Do NOT post the acceptance text yourself.
   Do NOT post `recheck`.

The bot accepts a PR comment in this format (for the human to post):
```
I have read the CLA Document and I hereby sign the CLA
```

## PR / CI flow (fork specifics)

1. After creating the draft PR, check both `gh pr checks <PR#>` **and**
   `gh run list --repo jaylfc/taOS --branch <branch>` - the matrix run may not show in
   `pr checks` while it awaits approval.
2. **If the CI run shows `action_required`**: the first-time-contributor workflow-approval policy
   is blocking it. Surface this to the human - do not re-poll, re-push, or re-create the PR.
   Lightweight checks (CLA, Gitar, CodeRabbit) run independently and don't need approval.
3. Once code is done and local tests pass, `gh pr ready`. Address review feedback with additional
   commits on the same branch. The maintainer merges upstream.

### Do not spend turns waiting

You pay per turn. Spend them on judgement (reading a diff, diagnosing a failure,
deciding whether a green check is trustworthy), not on waiting or repetition.

- **Do not poll CI in a loop.** `gh pr checks <PR#> --watch` blocks in one call
  and returns when the run finishes. Re-running `gh pr checks` every minute
  costs a turn each time and tells you nothing new. (A malformed watch once
  exited early and reported success while shards were still running, so read
  the final status rather than trusting that the command returned.)
- **If you have hand-verified the same property twice, write the test.** Two
  manual checks is the signal. A test costs nothing per run; re-reading costs
  every time.
- **Ask the narrow question.** A targeted `grep` or a symbol-level diff against
  `origin/dev` usually decides the matter far more cheaply than reading a whole
  diff. Work out what single fact settles it, then fetch only that.
- **Do not re-poll a blocked run.** See `action_required` above: surface it and
  stop. Repeatedly checking a run that is waiting on a human is pure cost.

## Post-Push Bot Review Cycle

After pushing a PR and marking it ready, automated bots review it. The reliable gate is
**Kilo Code Review + Gitar**. CodeRabbit is unreliable - a "pass" check can be a rate-limited
no-op, so never treat a CodeRabbit pass alone as evidence of review (its findings, when it does
run, still get folded). Qodo (`qodo-code-review`) appears on old PRs but is paused. Address all
findings **before** surfacing the PR for human maintainer review.

The rate-limited no-op is now also machine-gated: `.github/workflows/bot-review-gate.yml`
(implementation in `scripts/check_bot_review.py`) fails the `bot-review-gate` check when the
only CodeRabbit output on a PR is a rate-limit stub, and a companion `re-run-on-stub-comment`
job re-runs the gate against the PR head SHA when a stub comment lands *after* the initial
run went green. A red `bot-review-gate` check means the PR has no substantive CodeRabbit
review yet — wait for (or retrigger) a real review; do not merge on the stub.

Enforcement parity is a GitHub-side branch-protection setting, not in-repo config:
`bot-review-gate` is REQUIRED on `master` but only ADVISORY on `dev` (absent from dev's
`required_status_checks.contexts`), so a red check can merge through dev and block only at the
dev->master promotion. The hardening target is to add `bot-review-gate` to dev's required
contexts too; that edit is Jay's standing GitHub configuration (master is left unchanged) and
is not performed by a repo commit.

Two things to know before applying it (both recorded in the workflow header):

- **An override label must ship first.** `scripts/check_bot_review.py` fails on a CodeRabbit
  rate-limit stub, which is an infrastructure condition, not a code problem. Making the context
  required on `dev` before there is an escape hatch would block every merge to `dev` for the
  length of a rate-limit window.
- **Use the right API shape.** The contexts endpoint takes a top-level `contexts` ARRAY. A
  `-f required_status_checks='[...]'` string field is the wrong shape and the update silently
  does not apply. Send `{"contexts":[...]}` via `gh api -X PATCH ... --input <file>`, carrying
  the branch's existing contexts plus the new one -- the call replaces the whole list.

### Procedure

1. **Push PR and mark ready.** Wait ~10 minutes for bot reviews to complete.
2. **Pull bot findings - reviews AND inline comments, all bots.** The login FORM differs by API
   surface, so filter accordingly: the GraphQL command (`gh pr view --json`) returns BARE logins
   (`kilo-code-bot`, `gitar-bot`, `coderabbitai`, `qodo-code-review`), while the REST command
   (`gh api .../comments`) returns the `[bot]`-suffixed form (`kilo-code-bot[bot]`). The GraphQL
   filter below uses a substring `test()` so it matches the bare form; the REST command does not
   filter, so the suffix is harmless there.
   ```bash
   # Review summaries + issue comments (GraphQL, bare logins):
   gh pr view <PR#> --repo jaylfc/taOS --json reviews,comments --jq \
     '(.reviews[], .comments[]) | select((.author.login? // "") | test("kilo-code-bot|gitar-bot|coderabbitai|qodo")) | {login: .author.login, body: .body}'
   # Inline (line-anchored) review comments (REST, [bot]-suffixed) - Kilo/CodeRabbit post most findings here:
   gh api repos/jaylfc/taOS/pulls/<PR#>/comments --jq \
     '.[] | {login: (.user.login? // ""), path, line, body}'
   ```
   Check which commit SHA each bot actually reviewed - a "pass" on a stale commit is not a pass
   on your head.
3. **If issues found:** fix all findings in a single commit, re-run local tests, push,
   then go back to step 1 (max 2 cycles). If findings still persist after 2 cycles, stop -
   do not loop further; surface the remaining findings to the human.
4. **Only block for maintainer review when bots are clean** - 0 CRITICAL, 0 WARNING - and fold
   EVERY finding, nits and suggestions included. If a SUGGESTION is genuinely not applicable,
   note the rationale in a PR comment before blocking.

### Severity tiers

| Tier | Action |
|------|--------|
| CRITICAL | Must fix before blocking for review |
| WARNING | Must fix before blocking for review |
| SUGGESTION | Fix or explain why not applicable |

## PR lifecycle discipline (fold-first, rebase, closures)

The review pipeline only works if the open-PR set stays small and every finding
gets closed out. These rules are load-bearing; the maintainer gates on them.

### Fold-first: findings outrank new work

If ANY of your open PRs has an unaddressed maintainer fold list or bot finding,
addressing it comes BEFORE opening a new PR. Priority order each work session:

1. Fold open findings on existing PRs (maintainer comments first, then bot findings).
2. Rebase any of your PRs that show CONFLICTING against the base branch.
3. Only then start a new slice.

A finding folded within hours merges the same day; a finding left while you open
new PRs stalls the whole train behind it (the maintainer will not merge past an
open finding, ever).

Folding means code AND a reply: answer every numbered item in the PR thread
with the commit that addresses it or a concrete rebuttal. Code pushed without
in-thread replies leaves the fold formally open and the verdict at HOLD.

Bot review freshness is part of folding: check WHICH commit a bot actually
reviewed. A rate-limited or stale "SUCCESS" on an older head is not a pass -
after pushing fixes, re-trigger the review (for CodeRabbit: comment
`@coderabbitai review`).

### Rebase cadence and the stale-base rule

- dev moves fast. When your PR shows CONFLICTING, rebase onto current dev
  promptly - a CONFLICTING PR is invisible to the merge queue.
- A green CI run computed BEFORE the base branch moved is STALE. Two individually
  green PRs can conflict semantically with zero textual conflict (see the
  #2009/#1932 App-key incident, fixed in #2041). If dev advanced since your last
  CI run, rebase (or push an empty commit) so CI re-runs against the current base
  before asking for merge.
- Keep your open-PR count small (aim under 10). A wide-open set guarantees most
  of it is permanently CONFLICTING and re-review effort is wasted.

### Closing PRs: always link the successor

Never close a PR silently. In the closing comment state exactly one of:

- "Superseded by #NNNN" (and confirm every still-open finding from this PR is
  in the successor's scope), or
- "Landed via #NNNN" (when the content merged through another PR), or
- "Abandoned because <reason>".

A close without a successor link reads as lost work and forces the maintainer
into git forensics (this happened with #1927/#1924 - both were legitimate
"landed via" closures that looked like data loss for hours).

### Asking another team's agent a question

taOS depends on sibling services with their own maintainer agents (taOSmd, the
website). Their contracts are theirs to state, so ASK rather than inferring from
their source (pitfall 23). How to reach them depends on where you sit:

- **On the A2A bus** (internal agents): post on the relevant channel, name the
  agent, and expect a reply inside the hour.
- **Outside the bus** (external contributors): open an issue on
  `jaylfc/taos-agent-commons`, the private invite-only coordination repo. Label
  it `contract-question` and name the service. @taOS-dev sweeps it hourly and
  relays to the owning agent on the bus, then carries the answer back.
- `jaylfc/taosmd` is public with issues enabled, so a taosmd contract question
  can also go straight there.
- `jaylfc/taos-website` is private, so commons or the relay is the only route.

If a question sits unanswered for more than about two hours, escalate by also
raising it on the PR. The relay is a person-shaped hop and can stall; silence
should never be mistaken for progress.

This arrangement is TEMPORARY scaffolding. It retires when an external
contributor can hold a taOS identity and reach the bus directly, which is the
same capability as agent sharing. Do not build tooling that assumes it is
permanent.

### Verify before you claim, and compile before you PR

- An assertion with a tolerance (`abs(a - b) <= n`) does not test an invariant.
  It cannot tell correct behaviour from total failure. Assert equality and
  assert the resulting state (pitfall 20).
- Mocking internals or injecting unreproducible errors is fine. Mocking an
  external service CONTRACT is where tests lie. For sibling services (taOSmd,
  the website) the contract has a reachable owner on the A2A bus: ASK them
  rather than inferring from their source. Every mock failure in the #2062
  cycle was a guess at something one message would have answered. External
  contributors ask in the PR and the lead relays. For genuine third parties,
  capture a real response and commit it rather than composing a fixture from
  what your code expects. Then keep one feature detecting integration test per
  contract, or mark the mock provisional in code with its source and date. A
  follow-up issue does not count: it separates the caveat from the code. A
  green test over a fictional contract certifies the bug (pitfall 23).
- Typecheck or run the thing before opening the PR. A frontend change that does
  not compile wastes a full review round, and the executor now gates on
  `tsc --noEmit` for exactly that reason (pitfall 21).
- When the base branch has moved under you, rebase and read what changed before
  resolving conflicts. Taking the wrong side of a hunk silently reverts fixes
  that were just merged (pitfall 22).

### Scope honesty per slice

- The PR body states exactly what it ships versus what its issue scopes. Any
  deferred part gets an explicit deferral note AND a follow-up issue filed in
  the same push; the parent issue never auto-closes on a partial slice.
- Before committing, `git status` must show only intended source changes -
  runtime artifacts (anything under `data/`) never enter a commit, and a new
  component that writes under `data/` gitignores its directory in the same PR
  (pitfall 16).

### One PR per slice

- A fix and the test that proves it belong in ONE PR (pitfall 13 in
  docs/contributor-pitfalls.md).
- Never open a sibling PR for a slice that already has one. If a fresh branch is
  genuinely needed, open the new PR, link it, and close the old one with the
  supersede note in the same action.

Read docs/contributor-pitfalls.md before every PR - fold lists reference its
items by number (for example "pitfall 5").

## Common fix patterns

- **New route:** `routes/<feature>.py` with `router = APIRouter()` → register in
  `tinyagentos/routes/__init__.py::register_all_routers()` with `dependencies=_csrf`, honoring the
  ordering comments there (e.g. `agent_registry` before the generic `agents` `/{name}` route); do
  **not** add `include_router` inline in `app.py` → tests in `tests/test_<feature>.py` using the
  `client` fixture.
- **New store:** subclass `BaseStore` → set `SCHEMA` (first-open DDL) and `MIGRATIONS` (never
  reference a migration-added column in `SCHEMA`) → attach in the lifespan as `app.state.<name>` →
  mock in conftest if needed.
- **Config field:** add to config dataclass → update defaults + `to_dict()`/`from_dict()` →
  `test_config.py`.
- **Catalog entry:** `manifest.yaml` under `app-catalog/<category>/<id>/` → add to `catalog.yaml` →
  `pytest tests/test_catalog_sync.py`.
- **Debugging a test:** confirm it uses the async `client` fixture and that `tmp_data_dir` setup is
  complete; check the store's `init()`; isolate with `pytest <path>::<test> -v`.

## Frontend CSRF contract (session-authenticated SPA calls)

Every mutating route requires `X-CSRF-Token` on cookie-session requests (`verify_csrf` is attached
router-wide). Any SPA `fetch` that POSTs/PUTs/PATCHes/DELETEs must attach the double-submit header:
use `withCsrf(init)` from `desktop/src/lib/csrf.ts`, or the `taosFetch` wrapper
(`desktop/src/lib/taos-fetch.ts`) which applies it automatically. **A raw
`fetch("/api/...", {method:"POST"})` passes vitest but 403s "CSRF token missing" in
production** - this exact class shipped as a bug (#1977). Bearer-token
(agent JWT) calls are CSRF-exempt; only cookie sessions need the header.

pytest used to miss this class too, and no longer does - see "CSRF in tests" above.

## Agent auth model (Bearer JWT vs session)

Agents authenticate with grant-gated registry JWTs (`Authorization: Bearer ...`), which are
**CSRF-exempt**; human sessions use cookies + the CSRF header. Access derives from an ACTIVE grant
per `(agent, project)` - not from token claims - and no-grant yields an existence-hiding 404.
Agent-facing routes must ALSO be allowlisted in `tinyagentos/auth_middleware.py` (see Pitfalls).
Onboarding surfaces: project invites (`routes/project_invites.py`, URL + PIN), OS-level agent
invites (`/api/agents/invites`), and the auth-request consent flow. Source of truth:
`docs/agent-coordination.md` section "Agent API surface (scoped registry JWT)".

## Adapter verification vocabulary

Every adapter in `tinyagentos/adapters/__init__.py` declares
`verification_status: tested | beta | experimental | broken` (plus `tracking_issue`, required for
`broken`). `tested` and `beta` are the verified tiers (`verified_only` filtering returns both);
`broken` blocks the deploy wizard. A new or edited adapter must set this correctly - defaulting a
new framework to `experimental` is the norm until it passes round-trip verification.

## Version sync (release bumps)

Four files carry the version and must stay identical: `pyproject.toml`, `tinyagentos/__init__.py`,
`desktop/package.json` (all `1.0.0-beta.N`), and `uv.lock`'s `tinyagentos` entry in PEP 440
normalized form (`1.0.0bN`). Only pyproject vs uv.lock is test-gated
(`tests/test_version_lock_sync.py`); the other two drift silently, so check all four on any bump.

## Documentation gate

A gate blocks PRs that change certain feature code without a matching doc update
(configured in `docs/doc-gate.toml`). Rules marked **any change** also fire on a plain
modification; the rest fire only when a matching file is added or deleted. Test files
(`test_*.py`, `*.test.*`, `*.spec.*`, `__tests__/`) never trigger any rule.

| Change | Fires on | Requires editing |
|--------|----------|-----------------|
| Desktop app under `desktop/src/apps/` | add/remove | `README.md` |
| Route module under `tinyagentos/routes/` | any change | `docs/agent-coordination.md` |
| Installer under `tinyagentos/installers/` or `scripts/install*` | any change | `README.md` |
| Manifest under `app-catalog/` | any change | `README.md` |
| `tinyagentos/auth_middleware.py` (agent-token route allowlist) | any change | `docs/agent-coordination.md` |
| Anything under `tinyagentos/` or `desktop/src/` | any change | `CHANGELOG.md` or a `changelog.d/*.md` fragment |
| Agent registry, token auth, scope-requests store, `routes/agent_*.py`, `tinyagentos/mcp/` | any change | `docs/agent-manual/*.md` or `docs/agent-coordination.md` |
| `.github/workflows/*.yml`, `pyproject.toml`, `CONTRIBUTING.md` | any change | this skill or `docs/*.md` |
| `routes/desktop.py`, `routes/desktop_control.py`, `routes/taos_agent.py` | any change | `.claude/skills/taos-agent/*.md` or `docs/agent-manual/*.md` |
| `update_runner.py`, `auto_update.py`, `restart_orchestrator.py`, `scripts/collate_changelog.py` | any change | `docs/RELEASING.md`, a runbook, or another `docs/*.md` |
| `tinyagentos/worker/` | add/remove | `tinyagentos/worker/README.md` |

The changelog rule is the one that catches most PRs: any non-test change under
`tinyagentos/` or `desktop/src/` needs a `changelog.d/<pr>-<slug>.md` fragment (preferred
over editing `CHANGELOG.md` directly, which conflicts between PRs).

`.github/workflows/doc-gate.yml` is authoritative (a local `--no-verify` does not bypass it) and
also runs `scripts/check_schema_migrations.py` (the SCHEMA-before-migrations guard, see Pitfalls).

If your PR trips a rule and there is genuinely nothing to document, add a trailer:
```
Docs-Reviewed: no user-facing change, internal refactor only
```
The trailer passes **every** rule for that PR, so it is an escape hatch, not a shortcut:
the gate prints `doc-gate: trailer override used in <sha> by <author>: <why>` in its CI
log for each commit that carries one, and that line is reviewable. A reviewer may ask for
a real doc instead.

Run `scripts/install-git-hooks.sh` to enable local hooks (`.githooks/pre-commit` and
`.githooks/commit-msg`) so the gate runs before you push.

## Store wiring gate

A gate (`.github/workflows/store-wiring-gate.yml`, running `scripts/check_store_wiring.py`)
blocks PRs that add a new `BaseStore` subclass without wiring it into `tinyagentos/app.py`.
Routes reach stores ONLY via `request.app.state`, so an unwired store is unreachable dead
code. The check is name-level (the class name must appear in `app.py`) and polices only
classes added by the PR - pre-existing orphans are skipped.

For a store genuinely constructed elsewhere (tests, CLI, workers), waive it with a PR-body
trailer, which is logged by the gate:
```
Store-Unwired-Intentionally: <ClassName>, <why>
```

## Secret-ignores gate

A gate (`.github/workflows/secret-ignores-gate.yml`, running `scripts/check_secret_ignores.py`)
verifies that the committed `.gitignore` still protects known secret-shaped paths on every
promotion target. A `.gitignore` rule is the kind of file a rebase conflict can quietly drop
during a dev->master promotion while every test stays green and nothing builds red, so the
protection is asserted here, not assumed. The gate runs on push to `master`, `dev` and
`release/*` (a dropped rule fails the branch it lands on) and on PRs to those branches (a
conflict-resolution loss fails before the merge, since the merge commit's `.gitignore` is what
is checked).

Two signals, defense in depth:

- Every required protection rule must appear verbatim as an active line of `.gitignore`
  (`*.key`, `identity.json`, `*.p8`, `*credentials.json`, `*creds*.json`, the `*_private.*`
  key shapes, `secrets/`, `data/hub/`, and the rest listed in `REQUIRED_PATTERNS` in the
  script). Comment prose and narrower sibling rules do not satisfy a rule.
- A set of secret-shaped paths (`data/hub/identity.json`, `foo.key`, `creds.json`, `x.p8`,
  `y_credentials.json`, ...) must all be reported ignored by `git check-ignore`.

Removing any one protection pattern turns the gate red -- proven by a parametrized test that
drops each pattern from a copy of the real `.gitignore` and asserts the guard fails.

## Upstream conventions (from CONTRIBUTING.md)

- **Target branch is `dev`, not `master`.** `master` is the stable live-install track.
- Branch naming: `feat/<slug>` or `fix/<slug>`
- Conventional commits (see table above)
- No AI tool attribution in commits
- Python 3.11+ floor (pyproject.toml: `>=3.11,<3.14`). `match`/`case` and `X | None` union syntax
  are available. Most modules use `from __future__ import annotations`.
- Code style: match surrounding code, one concern per module
- Use `uv` for dependency management and test running: `uv sync --extra dev`, `uv run pytest`
- **Read `CONTRIBUTING.md` sections "Verifying your work" and "Avoiding collisions with
  other contributors".** They are the canonical statement of both; this file does not
  restate them so the two cannot drift. What follows is only the part specific to
  working here as an agent.

## Verification (agent specifics)

Green is a claim, not evidence. The repo-level cases are in CONTRIBUTING.md. These are
the ones that bite agents in particular:

- **Do not report work as done without a PR link.** A branch with commits and no PR is
  not delivered. A lane once announced completion on an empty branch; the check now runs
  before any completion is claimed, and the same standard applies to you.
- **A PR that passes CI can still be empty.** Before saying a card is finished, look at
  the actual diff and confirm it contains the change described.
- **Never infer merge conflicts from `git merge-tree`** against branches you have not
  fetched; it produces confident nonsense. Use GitHub's `mergeable_state`, and treat
  `unknown` as "not computed yet" and re-query rather than as an answer.
- **Check the exit status of the command that matters**, not the last one in a pipe.
  `cmd | tail && echo OK` prints OK when `cmd` failed.
- **An empty fetch is not a match.** If two files that cannot be identical hash the same,
  your request failed rather than the contents agreeing.
- **Verify a bot finding against the code before folding it.** Automated review is often
  wrong and always confident. Fold what is real, say plainly what is not.
- **Report honestly.** If tests fail, include the output. If a step was skipped, name it.
  "Done" means verified.

## Desktop SPA build + test

```bash
cd desktop
npm install                # Node.js 22+
npm run build              # tsc -b && vite build → outputs to static/desktop/
npm run test               # vitest (unit/component tests)
npm run test:e2e           # Playwright browser tests (needs running server)
```

## Adding an app to the catalog

1. Create directory: `app-catalog/<category>/<id>/`
2. Write `manifest.yaml` (use `app-catalog/agents/langroid/manifest.yaml` as template)
3. Add entry to `app-catalog/catalog.yaml`
4. Run `uv run pytest tests/test_catalog_sync.py -v`
5. Open a PR

All fields in `manifest.yaml` except `config_schema` are required. The `hardware_tiers` block
controls which hardware profiles see the app as recommended.

## Pitfalls

- **Full test suite is too large to run locally without `-n auto`.** Use the canonical
  gate: `uv run pytest tests/ --ignore=tests/e2e -n auto`. CI handles the full matrix.
- **Fork CI approval is first-time-only.** The repo's policy requires maintainer approval only for
  a contributor's FIRST fork PR (`action_required` on the CI run - surface to the human, do NOT
  poll or re-push). A returning contributor's CI runs automatically; if your checks show only bots
  green but no test/spa-build jobs at all, check `gh api "repos/jaylfc/taOS/actions/runs?head_sha=<sha>"`
  for `action_required` before assuming CI passed - bot-only green is NOT CI green.
- **No formatter/linter *config* yet, but CI is not silent.** There is no `.ruff.toml` and no
  `[tool.ruff]`/`[tool.black]`/`[tool.mypy]` section in `pyproject.toml` (ruff may land soon -
  check `pyproject.toml` before assuming). CI does run a `lint` job (`python -m compileall
  tinyagentos/`), so any syntax error fails CI, and `.githooks/pre-commit` + `.githooks/commit-msg`
  run the doc-gate + schema-migration checks locally (enable them with `scripts/install-git-hooks.sh`).
  Match the surrounding code style manually.
- **Secrets have a dedicated store.** `tinyagentos/secrets.py` (routes in
  `tinyagentos/routes/secrets.py`, attached as `app.state.secrets`) is the credential store. Store
  credentials there - never in config or in code.
- **CONTRIBUTING.md** says Python 3.10+, but `pyproject.toml` requires `>=3.11,<3.14`.
  Python 3.11 is the effective floor.
- **Routes do not import stores directly.** They access them via `request.app.state`.
  This is a common mistake - check existing routes for the pattern.
- **`static/desktop/` is gitignored.** The SPA build output is a generated artifact.
  The conftest in `tests/` stubs the SPA build output so backend tests don't need `npm run build`.
- **CSRF is HTTP-only; keep it websocket-safe.** `register_all_routers` attaches
  `dependencies=[Depends(verify_csrf)]` to *every* router, including ones with `@router.websocket`
  routes. `verify_csrf` must be typed `HTTPConnection` (the shared base of `Request` and
  `WebSocket`) so it is injectable on both scopes and can skip when there is no HTTP method - a
  plain `Request` param (or `Request | None`) `TypeError`s on a websocket route and breaks it (this
  is exactly what broke `/ws/chat`). Do session auth *inside* the handler (see `chat_ws` in
  `routes/chat.py`). Note: tests bypass CSRF via an autouse conftest patch, so a local green run
  does **not** prove CSRF behavior - only `tests/test_csrf.py` exercises the real check.
- **SCHEMA-before-migrations can brick boot.** `BaseStore.init()` runs `SCHEMA` before
  `MIGRATIONS`/`_post_init`, so a `CREATE INDEX` in `SCHEMA` on a migration-added column bricks boot
  on an *existing* DB while every fresh-DB test still passes. `scripts/check_schema_migrations.py`
  (doc-gate + pre-commit) guards this. Rule: never reference a migration-added column in `SCHEMA`;
  retrofit columns in a guarded `_post_init` (`PRAGMA table_info` + `ALTER` only if absent); and
  **test schema changes over an existing pre-change DB, not just a fresh one.**
- **Worktree test shadowing.** `tests/conftest.py` imports `tinyagentos` from the installed editable
  package (the main checkout). Running `pytest` in a worktree with the main venv tests the **wrong**
  code. Always `uv run pytest` from the worktree root, and sanity-check with
  `uv run python -c "import tinyagentos; print(tinyagentos.__file__)"` that the path is the worktree.
- **Agent-token route allowlist.** Agent/registry JWTs only reach explicitly allowlisted
  `(method, path)` pairs in `tinyagentos/auth_middleware.py`. A new agent-facing *mutating* route
  silently 401s until it is added there - and editing that file trips the doc-gate `agent-api` rule
  (update `docs/agent-coordination.md`).
- **Scope vocab lives in two synced places.** `_ALLOWED_SCOPES` (`routes/agent_registry.py`) and
  `VALID_SCOPES` (`routes/agent_auth_requests.py`) must stay equal - a test asserts
  `set(_ALLOWED_SCOPES) == set(VALID_SCOPES)`. Add any new scope to **both**.
- **The data layer is many separate SQLite files** - one file per store, not one shared DB. There is
  no cross-store SQL and migrations are per-store.

## Issue triage

taOS-specific rules only: verify the issue is still open and **unassigned**, comment
"Working on this - will open a draft PR" before starting, and sync from upstream `dev` first.

## First-run setup

```bash
git clone https://github.com/jaylfc/taOS.git
cd taOS
uv sync --extra dev
cd desktop && npm install && npm run build && cd ..
uv run pytest tests/ --ignore=tests/e2e -n auto
```

Python 3.11+ and Node.js 22+ are required.
