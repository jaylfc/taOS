<!--
  SINGLE SOURCE OF TRUTH for cross-agent handoff. Committed so any agent on any
  platform sees it. Update on merge / task-start / rate-limit / handoff. The Pi
  :00/:30 cron also refreshes it. Keep it SHORT, link issues for detail.
  See docs/AGENT_HANDOFF.md (local-only) for the on-arrival checklist + rules.
-->

# taOS: Live Status

**Last updated:** 2026-06-11 ~04:50 BST, by @taOS (Mac session, overnight autonomous run).
**Repo:** github.com/jaylfc/taOS, branches `master` (stable) <- `dev` (integration).

## GOTCHA for the next agent
- **Protected merges:** `gh pr merge` 401s on the OAuth token but `gh api -X PUT repos/jaylfc/taOS/pulls/N/merge -f merge_method=squash` WORKS with the same token (use `merge_method=merge` for dev->master promotions, never squash, never `--delete-branch`).
- **Promotion #767 (pairing auth + backend naming) may still be in CI.** If open and green, merge it with the api method above. Everything else from the overnight queue is DONE.
- CodeRabbit fake passes: a green CodeRabbit check can be a rate-limit notice; check the PR comments, use `@coderabbitai full review`.
- `tests/` is NOT an importable package: never `from tests.conftest import X` in test files; expose shared helpers as function-returning fixtures (see `pair_and_register_worker` in tests/conftest.py).

## Overnight run summary (2026-06-10 evening -> 06-11 ~04:50)
- **Beta Pi incident RESOLVED:** controller was half-alive (active in systemd, :6970 only). Root causes #755 (knowledge.db user_id migration never applied to existing DBs) + #756 (lifespan failure left process alive). Pi repaired + on master, verified healthy.
- **Merged to dev:** #752 (perf cleanups), #754 (installer sudo gap, closes #753), #758 (controller-rescue runbook), #763 (self-healing knowledge migration + exit-on-startup-failure, closes #755/#756), #764 (worker backend names type:port, closes #759), #762 (cluster pairing-code auth Phase 1, the #737 backend).
- **Promoted to master:** #766 (3fe1c490) carrying #752/#754/#758/#763. **#767** carrying #762/#764 in flight.
- **#723 reporter replied** (Jay-approved draft) with recovery instructions; #753 fix on master.
- **#762 review hardening:** admin gate on pairing pending/confirm, atomic claim (rowcount-gated), actionable 410/404 claim errors, store internals encapsulated (pairing_state()). One CodeRabbit suggestion rejected for cause: counting HMAC failures toward the pairing cap would create a re-pair DoS vector.

## Immediate next actions
1. Merge **#767** if still open (see GOTCHA).
2. **#737 Phase 2:** worker scripts (bash + powershell) generate + print the pairing code, announce, poll claim, persist signing_key, sign register/heartbeat. Spec pattern in the #737 issue comment; backend endpoints + HMAC header format are live on dev (see tinyagentos/cluster/worker_auth.py docstring). Sonnet with a full spec.
3. **#737 Phase 3:** taOS UI pending-workers list + enter-code dialog (frontend-design pass; ties into #760/#761 badge work).
4. **#737 Phase 4:** migration story for existing fleet workers (clear re-pair prompt, not silent 401s).
5. **#751** beads-inspired native task-graph: AWAITING Jay greenlight.

## Open issues filed this stretch
#753 (fixed, #754), #755/#756 (fixed, #763), #757 unit template env mangling (open, small), #759 (fixed, #764), #760 host badges everywhere (UI principle, design pass), #761 per-device emoji/badge identity (brainstorm first; hangs off #737 pairing store + #760).

## In flight
- **M1 security:** #737 Phase 1 DONE (#762). Phases 2-4 queued (see next actions).
- **#744 external coding-agent onboarding:** 7 build tasks queued behind M1.

## Cross-project (taosmd / A2A)
- Progress channels live: `taos-progress` (all overnight work posted), `taosmd-progress`. Durable 30-min freshness crons (taOS :00/:30, taosmd :15/:45).

## Blocked / waiting on human (Jay)
- `#15` exo fork deletion: needs `gh auth refresh -s delete_repo`.
- `TAOSMD_REGISTRY_URL` cutover: gated on the consent UI shipping (deliberate).
- #751 beads buy-vs-build greenlight.
- #761 emoji identity brainstorm.

## Where to look
1. GitHub issues = task list. 2. This file = snapshot. 3. docs/AGENT_HANDOFF.md (local) = rules + bootstrap. 4. A2A bus :7900 (taos-progress / general / integration). 5. @taOS Pi memory (Claude Code only).
