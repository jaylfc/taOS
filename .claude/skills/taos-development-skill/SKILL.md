---
name: taos-development-skill
description: TinyAgentOS (taOS) architecture map, contribution workflow, testing guide, common fix patterns, and coding conventions. Load when contributing to taOS - PRs, bug fixes, features, catalog additions.
---

# taos-development-skill

Procedures and architecture for contributing to
[TinyAgentOS](https://github.com/jaylfc/taOS). The non-negotiable rules live in
`soul.md`; this skill is the *how*.

> uses approximate counts (~N) as rough orientation only - actual numbers rot fast in a
> living repo. Trust the tree, not tallies.

## Repository layout

```
<clone>/
  tinyagentos/                 ← server package
    app.py                     ← FastAPI app factory: lifespan, route registration
    config.py                  ← Platform config, hardware detection
    routes/                    ← one APIRouter module per feature area (~86 modules)
    templates/                 ← minimal: agent_debugger.html only (frontend is React SPA)
    channel_hub/               ← framework-agnostic messaging: connectors + MessageRouter
    adapters/                  ← thin per-framework agent adapters (~25 lines each)
    cluster/                   ← distributed compute: worker registry, task routing, GPU lease
    worker/                    ← cross-platform worker apps (system tray, Android, iOS)
    stores/                    ← data layer: aiosqlite (SQLite), one store per concern
    chat/ projects/ mcp/       ← chat, project board/canvas/A2A, MCP proxy+permissions
    installers/ containers/    ← model/app installers; Docker + LXC backends
    migrations/                ← DB migrations
  desktop/                     ← React + TypeScript SPA (Vite)
  app-catalog/                 ← YAML app manifests + catalog.yaml (~108 apps)
  tests/                       ← pytest suite (~3,590 tests)
  docs/                        ← documentation; agent manual compiled from docs/agent-manual/
```

## Key architectural patterns

- **Routes** - each `routes/*.py` is an `APIRouter` registered in `app.py`'s `create_app()`.
  `async def` handlers, `await` all I/O, Pydantic request/response models. Routes access stores
  via `request.app.state` (dependency injection set up in the app lifespan) - they do **not**
  import stores directly. **No cross-importing between route modules.**
- **Stores** - SQLite via `aiosqlite`, each with `init()`/`close()`, attached to
  `request.app.state` in the lifespan (`app.state.metrics`, `app.state.secrets`, …).
- **Config** - `AppConfig` dataclass in `config.py`; YAML serialisation; async-locked saves via
  `save_config_locked()`; typed backends (`rkllama`, `ollama`, `openai`, `anthropic`, …).
- **Templates** - **Pico CSS utility classes only** (no other CSS framework). htmx (`hx-get`,
  `hx-target`, `hx-swap`) for dynamic partials. Semantic HTML; ARIA labels on interactive elements
  without visible text. Templates are minimal - the frontend is a React SPA.
- **Frontend** - React + TypeScript SPA in `desktop/`. Built with Vite: `npm run build` outputs
  to `static/desktop/` (gitignored). For development: `npm run dev` serves with hot reload on
  port 5173. One concern per component; API calls in dedicated hooks or service files.
- **Cluster** - worker registration, routing to remote nodes, model archive/promotion on capable
  hardware, GPU lease claim/release, hardware-tier compatibility.

## Git workflow

### Before starting

1. **Sync from upstream:**
   ```bash
   git fetch origin dev
   git checkout dev
   git rebase origin/dev
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
   Do NOT wait for CI - fork PRs are gated behind maintainer workflow approval.
   Mark ready once the CODE is done and local tests pass.

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

After pushing a PR and marking it ready, automated bots (Kilo, CodeRabbit) run reviews.
Address their findings **before** surfacing the PR for human maintainer review - this
eliminates the wasteful push→block→manual-check→unblock→re-dispatch cycle.

### Procedure

1. **Push PR and mark ready.** Wait ~10 minutes for bot reviews to complete.
2. **Pull bot comments:**
   ```bash
   gh pr view <PR#> --repo jaylfc/taOS --json comments --jq \
     '.comments[] | select(.author.login == "kilo-code-bot" or .author.login == "coderabbitai[bot]")'
   ```
3. **If issues found:** fix all findings in a single commit, re-run local tests, push,
   then go back to step 1 (max 2 cycles).
4. **Only block for maintainer review when bots are clean** - 0 CRITICAL, 0 WARNING.
   If a SUGGESTION-only finding is genuinely not applicable, note the rationale in a
   PR comment before blocking.

### Severity tiers

| Tier | Action |
|------|--------|
| CRITICAL | Must fix before blocking for review |
| WARNING | Must fix before blocking for review |
| SUGGESTION | Fix or explain why not applicable |

### Time estimates

| Phase | Duration |
|-------|----------|
| First bot pass (Kilo + CodeRabbit) | ~10 min |
| Fix cycle (if needed) | ~5–10 min |
| Second bot pass (if re-pushed) | ~10 min |
| **Worst case (2 cycles)** | **~30 min** |

## Common fix patterns

- **New route:** `routes/<feature>.py` with `router = APIRouter()` → register in `create_app()` →
  tests in `tests/test_<feature>.py` using the `client` fixture.
- **New store:** class with `init()`/`close()` (aiosqlite) → attach in the lifespan → mock in
  conftest if needed.
- **Config field:** add to config dataclass → update defaults + `to_dict()`/`from_dict()` →
  `test_config.py`.
- **Catalog entry:** `manifest.yaml` under `app-catalog/<category>/<id>/` → add to `catalog.yaml` →
  `pytest tests/test_catalog_sync.py`.
- **Debugging a test:** confirm it uses the async `client` fixture and that `tmp_data_dir` setup is
  complete; check the store's `init()`; isolate with `pytest <path>::<test> -v`.

## Documentation gate

A gate blocks PRs that add or remove certain feature code without a matching doc update
(configured in `docs/doc-gate.toml`):

| Change | Requires editing |
|--------|-----------------|
| Desktop app under `desktop/src/apps/` added/removed | `README.md` |
| Route module under `tinyagentos/routes/` added/removed | `docs/agent-coordination.md` |
| Installer under `tinyagentos/installers/` or `scripts/install*` added/removed | `README.md` |
| Manifest under `app-catalog/` added/removed | `README.md` |

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
- **CI may show `action_required` on every PR from a fork.** GitHub requires maintainer approval
  for workflow runs from first-time contributor forks. This can re-trigger on each new PR even after
  previous PRs were approved - it's per-workflow-run, not per-contributor. Surface to the human;
  do NOT poll or re-push.
- **No lint/format tooling is configured.** There is no `.pre-commit-config.yaml`, no
  `.ruff.toml`, and no `[tool.ruff]`, `[tool.black]`, or `[tool.mypy]` section in
  `pyproject.toml`. Match the surrounding code style manually.
- **Secrets:** No dedicated secrets store in the current tree. The `stores/` directory pattern
  handles data access; secrets management may live in the MCP permissions model or be handled
  at the OS/deployment layer. When unsure, treat secrets as escalate-to-human.
- **CONTRIBUTING.md** says Python 3.10+, but `pyproject.toml` requires `>=3.11,<3.14`.
  Python 3.11 is the effective floor.
- **Routes do not import stores directly.** They access them via `request.app.state`.
  This is a common mistake - check existing routes for the pattern.
- **`static/desktop/` is gitignored.** The SPA build output is a generated artifact.
  The conftest in `tests/` stubs the SPA build output so backend tests don't need `npm run build`.

## Issue triage

### Finding actionable issues
1. Filter GitHub issues by `good first issue` or `help wanted` labels
2. Check issue age - fresh issues (< 2 weeks) have highest chance of being unclaimed
3. Read the issue body carefully - look for clear reproduction steps
4. Check if anyone is already assigned

### Difficulty estimation
- **Catalog app addition** (~30 min): New manifest.yaml + catalog.yaml entry
- **Bug fix** (1–3 hours): Reproduce → find root cause → fix + regression test
- **Feature** (3+ hours): Design → implement → tests → documentation

### Before starting work
1. Sync from upstream: `git fetch origin dev`
2. Verify the issue is still open and unassigned
3. Comment on the issue: "Working on this - will open a draft PR"

## First-run setup

```bash
git clone https://github.com/jaylfc/taOS.git
cd tinyagentos
uv sync --extra dev
cd desktop && npm install && npm run build && cd ..
uv run pytest tests/ --ignore=tests/e2e -n auto
```

Python 3.11+ and Node.js 22+ are required.
