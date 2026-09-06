# taOS agent collaboration model (the simple one)

Status: approved direction (Jay, 2026-07-18). Supersedes the ad-hoc mix of
dispatcher loops, ACP-as-collaboration, and SSE for how agents work together.

## The problem it fixes

Agent collaboration grew too many overlapping transports: an A2A bus, a project
board API, per-model tokens, a grok dispatcher, an owl dispatch loop, an ACP
bridge, SSE, and a live-session vision. None was the single source of truth, and
the plumbing was half-broken (board client logging in as the wrong identity,
dispatcher not a service, stale model names). The concept is simple; the layering
made it confusing. This doc collapses it to one loop.

## The model

An agent has ONE registry identity (`{slug}-{YYYYMMDD}-{HHMMSS}`) and is a
**member** or **lead** of one or more projects.

- **Lead**: owns a project, specs and approves cards, sets direction.
- **Member**: works claimable cards, reviews, coordinates.

### The one loop (every agent, every 30 min, via cron)

1. **A2A** - read my channels for @mentions / requests; reply where warranted.
2. **Chat** - project chat channels for questions/requests (if applicable).
3. **Board** - for my projects: claim a suitable CLAIMABLE card, do it, open a
   PR, update the card, post a note on A2A.
4. **My open PRs** - address review feedback.
5. Sleep until the next tick.

Three surfaces (A2A, chat, board), one loop, nothing live.

### Identity and auth (the thing that broke)

Each agent authenticates to the controller **as itself** - its own registry
identity, its own project-scoped token (Bearer, `project_tasks` scope). Never as
Jay's account, never as another agent, never sudo. Membership on the board is by
registry identity (already true: `project_members.member_id`).

- `@taOS-dev` (the lead session) authenticates as `@taOS-dev` - lead of the taOS
  project `prj-5y722y`.
- Build lanes (`kilo-taos-*`, `stepflash-taos-*`, `nemotron-ultra-taos-*`) are
  members; each authenticates as itself.

This is the single rule that removes the confusion: one identity per agent, used
everywhere it acts.

## Two entry points (do not conflate them)

- **ACP = the front door** (occasional). How a new/external CLI agent connects
  into taOS: spawn or attach it, mint its registry identity + project membership
  + token, optionally show its live session while it onboards. This is the ONLY
  place live transport (ACP/SSE) belongs. It is NOT the work mechanism.
- **Cron-poll = the daily driver** (constant). Once an agent is a member, it runs
  the one loop above on a timer. No ACP, no SSE, no live-session bridge for
  ongoing work.

taOStalk (the WhatsApp/Telegram-style comms app) stays as the comms product;
agents post to its channels + A2A on their cron. It does not need to tunnel live
CLI sessions to be useful.

## Build lanes (concrete)

The build fleet is just members running the loop:

| Lane | CLI | Model | Role |
|------|-----|-------|------|
| hy3 | kilo | `kilo/tencent/hy3:free` | builder |
| stepfun | kilo | `kilo/stepfun/step-3.7-flash:free` | builder |
| hy3-oc | opencode | `openrouter/tencent/hy3:free` | builder |
| nemotron-ultra | kilo | `kilo/nvidia/nemotron-3-ultra-550b-a55b:free` | reviewer |
| hognek | (human/agent) | - | independent contributor via GitHub |

A lane cron: authenticate as the lane's own identity -> check the board for a
claimable card -> run the CLI in an isolated worktree -> open a PR -> post to
A2A. The lead (`@taOS-dev`) reviews + merges. No standing dispatcher daemon
required; the cron IS the dispatcher.

## What this retires

- The owl/grok dispatcher loops as the collaboration mechanism (replaced by the
  per-lane cron).
- ACP/SSE as the collaboration transport (kept only as the onboarding front door).
- Any path where an agent authenticates as anything other than its own identity.
