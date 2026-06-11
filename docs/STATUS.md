<!--
  SINGLE SOURCE OF TRUTH for cross-agent handoff. Committed so any agent on any
  platform sees it. Update on merge / task-start / rate-limit / handoff. The
  session freshness cron (:08/:38) also refreshes it. Keep it SHORT, link issues.
  See docs/AGENT_HANDOFF.md for the on-arrival checklist + rules.
-->

# taOS: Live Status

**Last updated:** 2026-06-11 ~11:15 BST, by @taOS (Mac session).
**Repo:** github.com/jaylfc/taOS, branches `master` (stable) <- `dev` (integration). master tip 66688348; dev = master + a STATUS commit only.

## GOTCHA for the next agent
- **Protected merges:** `gh pr merge` 401s on the OAuth token but `gh api -X PUT repos/jaylfc/taOS/pulls/N/merge -f merge_method=squash` WORKS (use `merge_method=merge` for dev->master promotions; never squash a promotion, never `--delete-branch`).
- CodeRabbit fake passes: a green CodeRabbit check can be a rate-limit notice; check the PR comments, use `@coderabbitai full review`. Kilo often 504s ("Assistant request timed out") = infra flake, not findings.
- `tests/` is NOT an importable package: never `from tests.conftest import X`; expose shared helpers as function-returning fixtures.
- Worker onboarding now REQUIRES pairing (a worker prints a code, admin approves in Cluster, signing key minted) before register/heartbeat. Signing string + headers in tinyagentos/cluster/worker_auth.py; worker side in tinyagentos/worker/pairing.py. VALIDATED in production (#772 passed).
- **Pi Claude session is ARCHIVED and its crons are stopped.** Freshness rides ONLY on the active agent's session cron (re-arm on a new session); there is no Pi-side durable backstop. The Pi controller + A2A bus are services and keep running.

## Recently landed
- **#737 cluster-worker pairing auth:** Phase 1 backend (#762) + Phase 2 worker scripts/agent signing (#770) DONE and on master (#767, #775). E2E VALIDATED in production (#772 closed: real Pi controller + Fedora worker, full announce->confirm->claim->signed register/heartbeat, unsigned->401). Phases 3 (UI pending-workers + enter-code dialog) and 4 (fleet migration UX) remain.
- **Beta incident fixes on master:** #763 (knowledge user_id migration self-heals bricked installs + exit-on-startup-failure), #754 (installer sudo gap), #768 (installer re-run ownership / priv-esc), #752 (perf), #758 (controller-rescue runbook), #757 (prefetch placeholder).
- Pi controller was repaired during the incident; it is on an OLD master (93f395e2) and LACKS the pairing backend. Update it before using it as the #772 test controller.

## Immediate next actions
1. **#737 Phase 3** (UI pending-workers list + enter-code dialog): frontend-design pass, HELD for a design session with Jay (Apple-grade bar), ties into #760/#761 badges.
2. **#737 Phase 4** (fleet migration UX): existing workers re-pair once with a clear prompt, not silent 401s.
3. **#774 project/shelf registry:** design DONE + spec at docs/superpowers/specs/2026-06-11-project-shelf-registry-design.md (local, gitignored). taOSmd integration thread OPEN (integration channel msg 322): 3 contract questions (shelf create/archive shape; empty-shelf archive reversibility for link; carve-out re-key vs re-ingest). Implementation plan gated on their answers.
4. **#744** external coding-agent onboarding: taosmd side merged (their PR #151); our 7 build tasks queued.

## Open issues filed this stretch
#757 (fixed #771), #759 (fixed #764), #760 host badges everywhere (UI), #761 per-device emoji identity (brainstorm first), #772 fresh-install/pairing smoke (Pi+Fedora), #774 project/shelf registry (design done).

## Cross-project (taosmd / A2A)
- #744 taosmd side MERGED (their PR #151): grant matching on (canonical_id, project_id) + verified-claim project binding; the `agent` field on data endpoints is a TARGET SHELF, not the caller. Our 7 #744 build tasks queued.
- Progress channels live: `taos-progress`, `taosmd-progress`. Freshness crons: taOS session :08/:38 (re-armed 2026-06-11, was dark), Pi durable backstop NOT installed (decision pending Jay).

## Blocked / waiting on human (Jay)
- `#15` exo fork deletion: needs `gh auth refresh -s delete_repo`.
- `TAOSMD_REGISTRY_URL` cutover: gated on the consent UI shipping.
- #751 beads buy-vs-build greenlight; #761 emoji brainstorm; #774 -> taOSmd thread.
- Whether to install a durable Pi-side freshness cron as a backstop to the session cron.

## Where to look
1. GitHub issues = task list. 2. This file = snapshot. 3. docs/AGENT_HANDOFF.md = rules + bootstrap. 4. A2A bus :7900. 5. @taOS Pi memory (Claude Code only).
