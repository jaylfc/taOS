# Agent Handoff Playbook

**Why this exists.** Work on taOS runs across rate-limit-prone agents on different platforms (Claude Code, Cursor, Codex, web, etc.). When one hits a limit, another picks up. The failure mode to prevent: an incoming agent acting on **stale knowledge** — re-doing finished work, missing in-flight tasks, or clobbering a branch. This playbook + `STATUS.md` + GitHub issues make the project's state **durable and platform-independent** so a handoff never loses work.

The golden rule: **durable state lives in three committed/hosted places, not in any one agent's memory** — (1) GitHub issues, (2) `docs/STATUS.md`, (3) the A2A bus. If it isn't in one of those, the next agent can't see it.

---

## On arrival — orient before you act (5 steps, ~2 min)

Run these before touching anything:

1. **Read `docs/STATUS.md`** (repo root → docs/). Current branch tips, open PRs, in-flight work, blockers.
2. **`git fetch origin && git log origin/master..origin/dev --oneline`** — what's on dev not yet promoted.
3. **`gh issue list --state open --limit 40`** — the canonical task list. `gh pr list --state open` — what's mid-review.
4. **A2A bus tail** (live coordination): `curl -s "http://<pi>:7900/a2a/messages?thread=general&limit=15"` (also observability, integration). The Pi IP is in your private notes, not committed here.
5. **(Claude Code only) `~/.claude/.../memory/MEMORY.md`** — durable context index. Other platforms: skip; everything you need is in 1–4.

Only after those: pick the top unblocked GitHub issue, or continue what `STATUS.md` says is in flight.

---

## Identity & non-negotiable rules

- **Git identity:** `user.name=jaylfc`, `user.email=jaylfc25@gmail.com`. ALL activity appears as jaylfc.
- **No AI attribution** anywhere — commits, PR bodies, issue comments. No "Co-Authored-By: Claude", no "Generated with…". Public repos must read as fully human-authored.
- **No secrets in git:** no IPs, tokens, credentials, Tailscale IPs, env-specific config. The Pi IP and bus URL stay out of committed files.
- **Branch policy:** small fixes → straight to `dev`. Features/refactors/redesigns → a branch + PR to `dev`. `master` is **protected** — promote only via a `dev`→`master` PR (squash). **NEVER `--delete-branch` on a dev→master PR** — deleting `dev` auto-closes every open PR that targets it.
- **Verify before claiming done:** run the tests/commands, paste real output. Evidence before assertions.

---

## When YOU get rate-limited — hand off cleanly (do this the moment you see the limit warning, if you still can)

1. **Commit or stash WIP** on a branch (never leave uncommitted work that only your session knows about). Push it.
2. **Update `docs/STATUS.md`**: move your task to "In flight" with the branch name + exactly where you stopped + the next concrete step.
3. **Post one A2A note** as your handle: what you finished, what's mid-flight, the branch, the next step.
4. **(Claude Code) update memory** if a durable fact changed.

If the limit hits before you can do this, the incoming agent recovers from: last pushed commit + open PR + `STATUS.md` + issues. That's why you push early and often.

---

## The freshness cron (keeps the durable layer honest)

An hourly sweep (session-scoped on the active agent; the Pi's :00/:30 cron is the durable backstop) re-checks README / docs / memory / `STATUS.md` against merged commits and fixes trivial drift, opens PRs for bigger rewrites. If you are the active driver, keep it armed. Its job is to ensure steps 1–5 above never read stale.

---

## Task hygiene — so nothing is lost

- **Every feature idea, bug, or TODO → a GitHub issue immediately.** Ideas in chat or memory evaporate across a handoff; issues don't. Label them (`feature`, `bug`, `security`, `docs`, `infra`).
- **One issue = one pickup-able unit** with enough context that a cold agent can start it.
- `STATUS.md` links to issues; it does not duplicate them.

---

## The durable stores at a glance

| Store | Scope | Visible to | Use for |
|-------|-------|-----------|---------|
| GitHub issues | canonical task list | every platform | backlog, features, bugs, audit findings |
| `docs/STATUS.md` | current snapshot | every platform (in repo) | "where are we right now" |
| `docs/AGENT_HANDOFF.md` | the rules + protocol | every platform (in repo) | onboarding, identity, hop protocol |
| A2A bus (:7900) | live coordination | the 3 bus agents | real-time @mentions, decisions |
| @taOS Pi memory | durable context | Claude Code only | per-session continuity for CC |
