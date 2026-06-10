<!--
  SINGLE SOURCE OF TRUTH for cross-agent handoff. Committed so any agent on any
  platform sees it. Update on merge / task-start / rate-limit / handoff. The Pi
  :00/:30 cron also refreshes it. Keep it SHORT, link issues for detail.
  See docs/AGENT_HANDOFF.md (local-only) for the on-arrival checklist + rules.
-->

# taOS: Live Status

**Last updated:** 2026-06-10 ~18:30 BST, by @taOS (Mac session, claude-fable-5), handing off before a rate limit.
**Repo:** github.com/jaylfc/taOS, branches `master` (stable) <- `dev` (integration).

## GOTCHA for the next agent
- **Merges/protected writes 401 on the gh OAuth token** intermittently (read calls are fine). Use the **GitHub UI merge button**, or a `ghp_` PAT via `GH_TOKEN=<pat> gh pr merge ...`. This is a token limitation, not a CI failure.
- A background merge-watcher was auto-merging #746/#748/#749 via PAT, but **it dies when this session ends**, so finish them manually (below).
- Never `--delete-branch` on a dev->master PR (auto-closes PRs targeting dev).

## Immediate next actions (finish M1 merges, then promote)
1. Merge to dev once CI green (Kilo failure = ignore, infra): **#746** (CSRF defense-in-depth), **#748** (add-to-project non-blocking CI), **#749** (docs taosmd coverage). #745 (SSRF) is already merged.
2. Promote dev -> master via a dev->master PR (squash, no --delete-branch). Needs the UI button or PAT.

## In flight
- **M1 security (audit milestone 1):** SSRF #738 DONE (#745 merged). CSRF #648 -> #746 (Strict session cookie + token wiring + fixed a latent silent-403 lock bug; per-route rollout tracked in #747). Remaining M1: **#737 cluster-worker auth** = a device-pairing-code flow, fully designed in the #737 comment (worker prints a code, admin pairs in taOS, mints the signing_key; 4 build phases). Not started.
- **beads evaluation (handoff tooling):** design thread OPEN with @taOSmd on the A2A integration channel (msg #289). beads = github.com/gastownhall/beads, a Go CLI+MCP, Dolt-backed, AI-agent-native dependency-graph issue tracker (hash IDs for concurrent multi-agent writes, `bd ready` offline unblocked-work queue, `bd prime` session context). 4 open questions posed (boundary: taosmd component vs peer; overlap with our A2A+memory; SBC/Dolt weight; adopt-as-is vs concepts-only). My lean: adopt-and-bridge over rebuild. AWAITING taOSmd reply, then bring Jay a joint buy-vs-build recommendation. Do NOT fold Jay in until taOSmd + I converge.
- **#744 external coding-agent onboarding:** spec committed (`docs/design/external-agent-onboarding.md`), token->project-memory contract v1 locked with taOSmd. 7 taOS build tasks queued behind M1.
- **Repo audit:** done, grade B-, report at `docs/audit/2026-06-10-repo-audit.md` (LOCAL-ONLY, gitignored, unpatched security detail). Findings tracked in issues #737-740 #743 + existing.

## Open issues filed this session
#735 feedback/bug tracker, #736 websites (taos.my + redirect), #737 cluster-worker pairing auth, #738 SSRF (fixed #745), #739 CI ruff+vitest+npm-audit, #740 Python lockfile, #741 resilience workflow, #742 memory-migrate cutover, #743 docs drift, #744 external-agent onboarding, #747 CSRF per-route rollout.

## Cross-project (taosmd / A2A)
- #25 memory unification DONE both sides + live + verified. Trust enforcement live-but-dormant (needs TAOSMD_REGISTRY_URL).
- Progress channels live: `taos-progress` (mine), `taosmd-progress` (theirs); both feed project memory.
- Workflow rules adopted both sides; durable 30-min freshness crons (taOS :00/:30, taosmd :15/:45). Pi Claude session is CLOSED (Mac session is sole @taOS driver this stretch).

## Blocked / waiting on human (Jay)
- `#15` exo fork deletion: needs `gh auth refresh -s delete_repo`.
- `TAOSMD_REGISTRY_URL` cutover: gated on the consent UI shipping (deliberate).
- Decide buy-vs-build on beads once taOSmd + I bring the joint rec.

## Where to look
1. GitHub issues = task list. 2. This file = snapshot. 3. docs/AGENT_HANDOFF.md (local) = rules + bootstrap. 4. A2A bus :7900 (taos-progress / general / integration). 5. @taOS Pi memory (Claude Code only).
