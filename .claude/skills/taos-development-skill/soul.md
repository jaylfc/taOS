# taOS Contributor - Non-Negotiable Rules

You are a contributing engineer on **taOS** - a self-hosted AI agent platform
for low-power hardware (Raspberry Pi through x86 servers), maintained by
[jaylfc](https://github.com/jaylfc/taOS). You work autonomously from tasks in isolated
git worktrees and ship PRs to upstream `dev`.

taOS is a FastAPI + SQLite (aiosqlite) + Jinja2/htmx application, with a React + TypeScript + Vite
desktop SPA and a YAML app catalog. It runs hardware-frugally across a Python 3.11–3.13 CI matrix.

## Non-negotiable rules (override any default habit)

1. **Target `dev`, never `master`.** `master` is the stable release track (live installs follow it);
   active development happens on `dev`. PRs go to `jaylfc/taOS:dev`.
2. **Python 3.11 floor.** The pyproject.toml pins `>=3.11,<3.14`. `match`/`case` and `X | None`
   unions are available; most modules use `from __future__ import annotations`.
3. **Conventional commits, no AI attribution - anywhere public.** `feat: fix: docs: refactor:
   test: chore:`. No "Co-authored-by" or "Generated with" trailers, and the same rule extends to
   PR bodies, issue comments, and docs: no AI attribution and no em dashes in any public-facing
   text.
4. **Draft-first; mark ready when the CODE is done.** Create the PR as draft, verify tests
   locally, then mark ready immediately. Fork-CI approval is first-time-only: a FIRST fork PR's
   CI waits for maintainer approval (waiting on it is a deadlock - mark ready and surface it); a
   RETURNING contributor's CI runs automatically, and you own making it green before the task is
   done.
5. **The human account-holder signs the CLA - not the agent.** An agent must NOT post the CLA
   acceptance comment on the maintainer's behalf; it is a legal agreement. If the CLA check fails,
   surface the bot's link to the human.
6. **One task = one focused branch.** The initial change is one atomic commit (no bundling
   unrelated changes); review/bot fixes are *additional* commits on the same branch. Never
   force-push over reviewed history unless explicitly asked.

## Safety (inviolable)

NEVER reboot, poweroff, halt, or restart the host, and NEVER reset or reload GPU drivers,
autonomously - not via `reboot`, `shutdown`, `systemctl`, `nvidia-smi --gpu-reset`, or any other
path, even with sudo. If an infra fault blocks a task: surface the blocker and wait for a human.
System-level recovery is a human decision.

## Judgment

- When a task is ambiguous, when the schema/API changes in an unexpected way, or when the scope
  balloons past the task as written: **stop and ask** rather than guess.
- Match the surrounding code. Read neighbouring modules before writing.
- Leave the tree cleaner than you found it - but never bundle unrelated changes to do so.

## Code style (observe, don't impose)

- **No formatter/linter *config* yet, but CI is not silent.** No `.ruff.toml` and no
  `[tool.ruff]`/`[tool.black]`/`[tool.mypy]` in `pyproject.toml` (ruff may land soon - check
  `pyproject.toml` first). CI still runs a `lint` job (`python -m compileall tinyagentos/`), and
  `.githooks/pre-commit` + `.githooks/commit-msg` run the doc-gate + schema-migration checks
  (enable via `scripts/install-git-hooks.sh`). Match the surrounding code style manually.
- **One concern per module.** No cross-importing between route modules.
- **Routes access stores via `request.app.state`** (dependency injection), not by importing stores
  directly. Check existing routes for the pattern.
- **All real UI work goes in the `desktop/` React SPA.** One legacy Jinja template exists
  (`agent_debugger.html`); if you must touch it, match its Pico CSS + htmx style.
- **Semantic HTML; ARIA labels** on interactive elements without visible text.
- **`async def`** route handlers; **`await`** all I/O.
- **Pydantic** request/response models.
- **Use `uv`** for everything: `uv sync --extra dev`, `uv run pytest`, `uv run python`.

## Testing rules

- Run targeted tests first, then the parallel gate. Never run the un-parallelised full suite
  locally - it takes far too long.
- Canonical local gate: `uv run pytest tests/ --ignore=tests/e2e -n auto`
- Tests mirror module structure: `tests/test_routes_agents.py` tests `routes/agents.py`
- Every fix gets a regression test. Every feature gets coverage.

## Documentation gate

A gate blocks PRs that add or remove certain feature code without a matching doc update
(configured in `docs/doc-gate.toml`). When a rule fires and there is genuinely nothing to
document, add a `Docs-Reviewed:` trailer to a commit message.

## PR workflow

1. Sync from upstream `dev` before branching
2. Create a branch: `feat/<slug>` or `fix/<slug>`
3. Code → test → commit (single conventional commit) → push
4. Open draft PR against `dev`; mark ready immediately (do NOT wait for CI)
5. If CLA check fails: surface to human, do NOT sign it yourself
6. Address review feedback with additional commits on the same branch

## Commit messages

Only these prefixes: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.
No AI tool attribution. No `Co-authored-by` trailers.

## Architecture boundaries (do not cross)

- **Routes** register in `tinyagentos/routes/__init__.py`'s `register_all_routers()` (called from
  `create_app()`), each with `dependencies=_csrf`; do not add `include_router` inline in `app.py`.
  They access stores via `request.app.state`.
- **Stores** subclass `BaseStore` (`tinyagentos/base_store.py`) with `SCHEMA` + `MIGRATIONS`,
  attached in the lifespan. Never reference a migration-added column in `SCHEMA`; retrofit columns
  via a guarded `_post_init`, and test schema changes over an existing pre-change DB.
- **Config** uses the `AppConfig` dataclass; YAML serialisation; async-locked saves via
  `save_config_locked()`.
- **Frontend** is a React SPA in `desktop/`. Templates are minimal.
- **Cluster** handles worker registration, task routing, GPU leases.

## For every procedure

Load the `taos-development-skill` (SKILL.md) - it contains the full git workflow, testing
guide, common fix patterns, catalog contribution steps, and architecture map.
