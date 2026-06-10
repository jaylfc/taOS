<!--
  SINGLE SOURCE OF TRUTH for cross-agent handoff. Committed so any agent on any
  platform sees it. Update on merge / task-start / rate-limit / handoff. The Pi
  :00/:30 cron also refreshes it. Keep it SHORT, link issues for detail.
  See docs/AGENT_HANDOFF.md (local-only) for the on-arrival checklist + rules.
-->

# taOS: Live Status

**Last updated:** 2026-06-10 ~23:40 BST, by @taOS (Mac session), at a rate-limit pause point.
**Repo:** github.com/jaylfc/taOS, branches `master` (stable) <- `dev` (integration).

## GOTCHA for the next agent
- **Protected merges:** `gh pr merge` 401s on the OAuth token but `gh api -X PUT repos/jaylfc/taOS/pulls/N/merge -f merge_method=squash` WORKS with the same token. No PAT needed. Never `--delete-branch` on a dev->master PR. Promote with `--merge`, not squash.
- **PR #762 (cluster pairing auth, #737 Phase 1) is OPEN, review half-done, NOT merged.** pairing_store.py reviewed clean by me; still to review: worker_auth.py, auth_middleware split, routes/cluster.py changes. CI was green-pending (lint/spa green, tests running). It is a SECURITY pr: finish the manual review, check Kilo+CodeRabbit comments, then merge via the gh api method.
- **Two sonnet agents may have died mid-build with this session.** Check `gh pr list` for: (a) fix for #755+#756 on branch `fix/knowledge-migration-and-dual-port-exit`, (b) fix for #759 on branch `fix/worker-backend-name-localhost`. If no PR exists, re-dispatch: issues #755, #756, #759 contain the complete analysis and fix design (they are the spec).
- CodeRabbit rate-limit fake-passes: a green CodeRabbit check can be a rate-limit notice. Check the PR comments; use `@coderabbitai full review`.

## Tonight's incident (resolved): beta Pi controller down
Root cause chain, all filed: knowledge.db predating user_id crashed init (schema index referenced the column before the migration could run, and the migration runner's baseline-at-latest would have skipped it anyway) = **#755**; uvicorn serve() returns instead of raising on lifespan failure so the dual-port gather left a half-alive process on :6970 with systemd showing active = **#756**. Pi repaired manually (ALTER TABLE + update to master 93f395e2) and verified healthy. py-spy is now installed in the Pi venv. New runbook: `docs/runbooks/controller-rescue.md` (#758, merged). **#755 fix must reach master before any beta user updates across the user_id boundary.**

## Immediate next actions (in order)
1. Finish review + merge **#762** (pairing auth Phase 1) when CI green.
2. Land the **#755+#756 fix** (re-dispatch if the agent died, see GOTCHA).
3. Land the **#759 fix** (same).
4. **Promote dev -> master** (carries #752 perf cleanups, #754 installer sudo fix, #758 runbook, plus whatever lands above). Use a dev->master PR with `--merge`.
5. **Post the approved #723 reply** (Jay approved the draft verbatim, it is in the session transcript and summarised in the issue context: two causes, #724 fixed the CHDIR loop, #753 tracked the no-sudo gap, recovery = re-run installer with sudo). Post AFTER #753's fix (#754) is on master via step 4.
6. #737 Phase 2 (worker scripts) once Phase 1 merged; spec pattern in the #737 comment.

## Merged to dev this session
#752 (approve-lock eviction + auth_requests indexes + shared httpx client), #754 (installer sudo gap, closes #753), #758 (controller rescue runbook).

## Open issues filed this session
#753 installer sudo gap (fixed by #754), #755 knowledge migration bricks updates (CRITICAL), #756 half-alive controller on startup failure, #757 unit template env mangling (0_BASE_IMAGE), #759 worker backend names embed localhost URLs, #760 host badges everywhere (UI principle, design pass), #761 per-device emoji/badge identity (brainstorm first, depends on #760 + #737 pairing store).

## In flight
- **M1 security:** #737 Phase 1 = PR #762 (open). Phases 2-4 queued (scripts, UI, migration).
- **#751** beads-inspired native task-graph: AWAITING Jay greenlight (buy vs build).
- **#744 external coding-agent onboarding:** 7 build tasks queued behind M1.

## Cross-project (taosmd / A2A)
- Progress channels live: `taos-progress` (incident + all merges posted), `taosmd-progress`. Durable 30-min freshness crons (taOS :00/:30, taosmd :15/:45).

## Blocked / waiting on human (Jay)
- `#15` exo fork deletion: needs `gh auth refresh -s delete_repo`.
- `TAOSMD_REGISTRY_URL` cutover: gated on the consent UI shipping (deliberate).
- #751 beads buy-vs-build greenlight.
- #761 emoji identity brainstorm.

## Where to look
1. GitHub issues = task list. 2. This file = snapshot. 3. docs/AGENT_HANDOFF.md (local) = rules + bootstrap. 4. A2A bus :7900 (taos-progress / general / integration). 5. @taOS Pi memory (Claude Code only).
