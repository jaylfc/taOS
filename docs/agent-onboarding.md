# Agent Onboarding

**Why this exists.** Work on taOS runs across rate-limit-prone agents on different platforms (Claude Code, Cursor, Codex, web, etc.). When one hits a limit, another picks up. The failure mode to prevent: an incoming agent acting on **stale knowledge**: re-doing finished work, missing in-flight tasks, or clobbering a branch. This playbook + GitHub issues make the project's state **durable and platform-independent** so a handoff never loses work.

The golden rule: **durable state lives in the project board `prj-5y722y` (canonical task list), with `docs/agent-onboarding.md`, the A2A bus, A2A `taos-progress`, and @taOS Pi memory as auxiliary stores. GitHub issues are secondary and always linked from the board.**

---

## Bootstrap (paste this into a fresh agent, or just tell it "read docs/agent-onboarding.md")

> You are taking over @taOS work on the taOS repo (`~/Development/tinyagentos`, GitHub `jaylfc/taOS`). Another agent was driving and may have hit a rate limit. Orient yourself from the durable state before doing anything; do not trust assumptions:
>
> 1. Read the rest of this file (`docs/agent-onboarding.md`): rules, identity, hop protocol. Follow it exactly.
> 2. `git fetch origin && git log origin/master..origin/dev --oneline`: what is on dev not yet promoted.
> 3. Check the board `prj-5y722y` (canonical task list) and `gh pr list --state open`.
> 4. A2A bus, live coordination: tail `taos-progress`, `general`, `observability`, `integration` (Pi is `jay@<pi>`, key auth, bus on).
> 5. (Claude Code only) read `~/.claude/projects/-*-tinyagentos/memory/MEMORY.md`.
>
> Then take the top unblocked card from the board or continue what durable state flags as in flight. Drop a status line in `taos-progress` when you start, finish, or learn something. Follow the Identity rules below to the letter.

---
## Identity & non-negotiable rules

- **@taOS is the project identity.** Every post uses the current seat's registry identity. The bus enforces `token sub == from` whenever a Bearer token is present, so posting as `@taOS` from outside a seat context produces 403s or unattributable posts.
- **Git identity:** `user.name=jaylfc`, `user.email=jaylfc25@gmail.com`. ALL activity appears as jaylfc.
- **No AI attribution** anywhere: commits, PR bodies, issue comments, A2A. No "Co-Authored-By: Claude", no "Generated with...". Public repos and the bus must read as fully human-authored.
- **Voice (anything posted publicly as Jay: commits, PRs, issues, A2A, docs, web copy): NO em dashes, ever.** Use commas, colons, parentheses, or two sentences instead. Strip the usual AI tells (no "it's not just X, it's Y", no "delve", no breathless hedging). For user-facing prose (release notes, web copy, replies), run it through the `content-humanizer` skill before posting. Keep internal terse-but-human.
- **Design:** any taOS or taOSmd dashboard / inspector / web UI work uses the `frontend-design` (impeccable) skill, kept offline / no-CDN friendly.
- **No secrets in git:** no IPs, tokens, credentials, Tailscale IPs, env-specific config. The Pi IP and bus URL stay out of committed files (they live in your private notes / this is why the bootstrap names them in chat, not in tracked code).
- **Branch policy:** small fixes go straight to `dev`. Features/refactors/redesigns get a branch + PR to `dev`. `master` is **protected**: promote only via a `dev`->`master` PR (squash). Protected-master merge needs a `ghp_` PAT or the GitHub UI button (the gh OAuth token 401s on that endpoint). **NEVER `--delete-branch` on a dev->master PR** (deleting `dev` auto-closes every open PR that targets it).
- **Verify before claiming done:** run the tests/commands, paste real output. Evidence before assertions.

---

## When YOU get rate-limited: hand off cleanly (do this the moment you see the limit warning, if you still can)

1. **Commit or stash WIP** on a branch (never leave uncommitted work that only your session knows about). Push it.
2. **Update the board**: move your card to "In flight" with the branch name + exactly where you stopped + the next concrete step.
3. **Post one A2A note** as your handle: what you finished, what's mid-flight, the branch, the next step.
4. **(Claude Code) update memory** if a durable fact changed.

If the limit hits before you can do this, the incoming agent recovers from: last pushed commit + open PR + the board + issues. That's why you push early and often.

---

## Freshness cron held under fleet HOLD (2026-08-31)

The hourly sweep is under fleet HOLD (Jay, 2026-08-24, reaffirmed 2026-08-30). Crons stay stopped; no re‑arm. The durable layer is swept manually when needed.

---

## Task hygiene: so nothing is lost

- **Every feature idea, bug, or TODO becomes a card on the project board `prj-5y722y` first.** GitHub issues are auxiliary and referenced from the board. Ideas in chat or memory evaporate across a handoff; the board persists. Label board cards (`feature`, `bug`, `security`, `docs`, `infra`).
- **One card = one pickup-able unit** with enough context that a cold agent can start it.
- The board links to issues; it does not duplicate them.

---

## A2A channels (use them; they feed the project memory)

The taosmd-hosted bus ingests messages into the project memory store, so posting there is also how progress becomes durable, searchable context.

- **`taos-progress`** (post here often): @taOS status updates, lessons learned, decisions, "starting X / finished Y / gotcha Z". One line when you start a task, one when you finish, one for anything non-obvious you learned. This is the running log that survives handoffs and lands in memory.
- **`general`**: cross-agent coordination and @mentions with @taOSmd / @hermes.
- **`observability`**: memory/bench/observability contract talk with @taOSmd.
- **`integration`**: cross-repo integration design.
- @taOSmd keeps its own **`taosmd-progress`** channel for the same purpose on its side.

## The durable stores at a glance

| Store | Scope | Visible to | Use for |
|-------|-------|-----------|---------|
| taOS project board `prj-5y722y` | canonical task list | every platform | backlog, features, bugs, audit findings |
| `docs/agent-onboarding.md` | the rules + protocol | every platform (in repo) | onboarding, identity, hop protocol |
| A2A `taos-progress` | running progress log | bus agents + project memory | status, lessons, decisions (feeds memory) |
| A2A bus | live coordination | the bus agents | real-time @mentions, decisions |
| @taOS Pi memory | durable context | Claude Code only | per-session continuity for CC |

---

## Commit conventions

- Subject line: imperative mood, lowercase, no period at the end. Max 72 chars.
- Body: explain what changed and why, not how (the diff shows how).
- Reference issues and PRs with `#123` or `GH-123`.
- Co-authors: none. No "Co-Authored-By", no "Generated with...".
- Conventional commits preferred: `fix(scope): description`, `feat(scope): description`, `docs: description`, `chore: description`.
- One logical change per commit. If you find yourself writing "and" in the subject, split it.

---

## Documentation standards

- User-facing docs live under `docs/` or the component's own README.
- Run prose through the `content-humanizer` skill before committing if it will be visible to users or contributors.
- Keep line length under 100 chars for plain text, 80 for code examples.
- Link to files with relative paths from the repo root (`docs/agent-onboarding.md`, not `/docs/agent-onboarding.md`).
- Do not commit screenshots or large binaries to the repo without checking with a lead first.

---

## Test requirements

Every PR must include tests for new behaviour. Run them with `uv run pytest <paths> -q` (the `dev` dependency group is installed by default; there is no `dev` extra).

- Mock at the narrowest scope: patch the specific function under test.
- Never patch the whole imported library.
- If the code catches library exceptions, keep the real exception classes.
- Add a test for every bug fix.

---

## Changelog fragments

A non-test change under `tinyagentos/` or `desktop/src/` requires a `changelog.d/<pr>-<slug>.md` or `changelog.d/tsk-<cardid>-<slug>.md` fragment containing one of the `### Added` / `### Changed` / `### Fixed` / `### Removed` / `### Security` headings and one bullet describing the change. Do not edit `CHANGELOG.md` directly.

---

## PR review requirements

Every PR targeting `dev` or `master` must clear the following before merge:

- CI green: `test (3.12)`, `test (3.13)`, `spa-build`, `lint`, `doc-gate`, `shards (3.12, 1-4)`, `shards (3.13, 1-4)`, `bot-review-gate` all passing.
- Human review: at least one project lead has approved.
- `bot-review-gate` is required, so it blocks on its own.

`bot-review-allow` is inert on any PR that already has a failed run. The real path is `--admin` with the justification posted first.

Do not merge a PR that is green on CI but has never been read by a human. Bot checks are signals, not substitutes for judgment.

---

## Kilo review policy

Kilo is the inline coding agent used during development. It posts a `Kilo Code Review` check run whose output has caught real defects. Read the output, never the `conclusion` field, and treat `Assistant service is unavailable` as an outage rather than a finding. Do not wait for Kilo output before merging.

Kilo output is not a substitute for human review or CI. If Kilo finds a problem, fix it before requesting human review.

When Kilo is inline during a development session, its suggestions are working hypotheses, not finished code. Review them, test them, then commit.

Treat Kilo inline suggestions as draft code, not committed truth.

---

## CodeRabbit review policy

CodeRabbit is an external review bot that posts on some PRs. Its behaviour is intermittent:

- Some PRs receive a genuine multi-item review with real findings.
- Other PRs receive only a rate-limit stub comment (a short notice that the review quota was exhausted).
- Some PRs receive no CodeRabbit output at all.

`scripts/check_bot_review.py` is the arbiter. It fetches the PR's CodeRabbit comments and reviews, distinguishes real review items from rate-limit stubs and auto-generated scaffolding, and exits:
- `0 PASS` when a real review exists.
- `0 PASS (absent, not stubbed)` when CodeRabbit is entirely absent, which is not reviewed and must not read as clean.
- `1 FAIL` when the only CodeRabbit output is a stub or scaffolding.
- `2 ERROR` on infrastructure failure.

Do not block on CodeRabbit, do not retrigger it, and its red is not clearable by retriggering.
