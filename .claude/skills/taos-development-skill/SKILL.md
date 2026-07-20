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

### Test conventions

- `conftest.py`: `tmp_data_dir` fixture creates temp config + SQLite
- `app` fixture: `create_app(data_dir=tmp_data_dir)`
- `client` fixture: `AsyncClient(transport=ASGITransport(app=app))` - async HTTP test client
- Module mirroring: `tests/test_agents.py` tests `routes/agents.py`
- SPA stubs: conftest creates stub `index.html`/`sw.js` so tests don't need `npm run build`
- E2E (Playwright) tests excluded from CI and local gate

### CI matrix

- Python 3.12 + 3.13 on every PR/push; 3.11 on nightly cron only
- GitHub Actions: `.github/workflows/ci.yml` in upstream repo
- Uses `uv sync --frozen` and `pytest -n auto`
- Also required: `spa-build` (npm build + tsc + **vitest** - a desktop type error or failing
  component test fails CI), a "Verify app starts" `create_app` import smoke, `lint`
  (`compileall`), and `cla`. The doc-gate is a separate workflow.

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

## Post-Push Bot Review Cycle

After pushing a PR and marking it ready, automated bots review it. The reliable gate is
**Kilo Code Review + Gitar**. CodeRabbit is unreliable - a "pass" check can be a rate-limited
no-op, so never treat a CodeRabbit pass alone as evidence of review (its findings, when it does
run, still get folded). Qodo (`qodo-code-review`) appears on old PRs but is paused. Address all
findings **before** surfacing the PR for human maintainer review.

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
`fetch("/api/...", {method:"POST"})` passes vitest AND pytest (tests bypass CSRF) but 403s
"CSRF token missing" in production** - this exact class shipped as a bug (#1977). Bearer-token
(agent JWT) calls are CSRF-exempt; only cookie sessions need the header.

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

A gate blocks PRs that add or remove certain feature code without a matching doc update
(configured in `docs/doc-gate.toml`):

| Change | Requires editing |
|--------|-----------------|
| Desktop app under `desktop/src/apps/` added/removed | `README.md` |
| Route module under `tinyagentos/routes/` added/removed | `docs/agent-coordination.md` |
| Installer under `tinyagentos/installers/` or `scripts/install*` added/removed | `README.md` |
| Manifest under `app-catalog/` added/removed | `README.md` |
| `tinyagentos/auth_middleware.py` (agent-token route allowlist) changed | `docs/agent-coordination.md` |

`.github/workflows/doc-gate.yml` is authoritative (a local `--no-verify` does not bypass it) and
also runs `scripts/check_schema_migrations.py` (the SCHEMA-before-migrations guard, see Pitfalls).

If your PR trips a rule and there is genuinely nothing to document, add a trailer:
```
Docs-Reviewed: no user-facing change, internal refactor only
```

Run `scripts/install-git-hooks.sh` to enable local hooks (`.githooks/pre-commit` and
`.githooks/commit-msg`) so the gate runs before you push.

## Upstream conventions (from CONTRIBUTING.md)

- **Target branch is `dev`, not `master`.** `master` is the stable live-install track.
- Branch naming: `feat/<slug>` or `fix/<slug>`
- Conventional commits (see table above)
- No AI tool attribution in commits
- Python 3.11+ floor (pyproject.toml: `>=3.11,<3.14`). `match`/`case` and `X | None` union syntax
  are available. Most modules use `from __future__ import annotations`.
- Code style: match surrounding code, one concern per module
- Use `uv` for dependency management and test running: `uv sync --extra dev`, `uv run pytest`

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
