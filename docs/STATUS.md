SINGLE SOURCE OF TRUTH for cross-agent handoff.
Last updated: 2026-06-13, @taOS (freshness sweep).

Branch tips: master=99cf786e. dev=355bb5ef (30 ahead of master; do NOT promote dev->master, Jay 2026-06-13: everything to dev only). On dev: Messages train, activity fix, deep-nav API, agent jobs 8/12/16/17, xdist CI fix (#839).

Session state: ACTIVE. A2A poll monitor ARMED. Freshness cron :08/:38 ARMED (now incl. 0f CodeRabbit retrigger). 5h resume-pair ARMED (primary 15:53, retry 16:12 local; session-scoped). 5h usage 78%, weekly 8%. Usage policy: push until ~90% (98% max), don't stop at 70; crons monitor-only past 90.
NOTE: a parallel session's freshness cron keeps reverting this file to a stale dev tip; if you see an old tip + "usage 47%", it is that churn, re-sync to the real dev tip.
WEBSITE: all 4 PRs merged to taos-website main (stats/changelog/nav/accessibility); Coolify redeploys taos.my.
CI: test suite parallelized via #839 (xdist -n auto), ~22 min -> ~13 min. CodeRabbit out of org credits -> rate-limits; freshness 0f retriggers oldest unreviewed PR with "@coderabbitai full review", never merge on a fake pass.
OPEN: ALL 26 agent jobs DONE. #838 = complete Messages-polish batch (jobs 24/21/22/23/26/10/19/18/13/9); #842 = agent manual templates (job 14). Both: CI+Kilo will pass; BLOCKED on a real CodeRabbit review (org credits exhausted) before merge to dev. jobs 8/12/16/17 + website (11/15/20/25 on taos-website main) already landed.

Done (since last STATUS.md update, 2026-06-13):
- Messages-polish train (jobs 1-7) ALL on dev via #826/#829/#830 direct + #833 integration (#827/#828/#831/#832); sub-PRs closed superseded, branches deleted.
- #783 VERIFIED FIXED on Pi: qwen 2.5 3b + 7b instruct rkllm pull to 100%, load, infer on NPU. CAVEAT: tested rkllama directly, NOT the store-UI /api/store install route.
- fix(activity): dedupe local node in scheduler + detect ARM SoC (RK3588) for CPU label (c55f9292, 4 tests). Pi shows it after next deploy (hardware profile cached).
- feat(desktop): deep-navigation API (?app= url + taos:open-app event), extracted to tested useDeepNavigation hook (14 tests). Tracked #836.
- Agent jobs done direct-to-dev: 8 Cmd+K switcher, 12 theme inventory (#837 merged), 16 CodeBlock tests, 17 update-ping toggle.
- Ideas filed: #796 benchmark pause/resume, #797 native phone, #798 native desktop shared-API, #799 TUI, #834 edit-before-send, #835 copy agent text, #836 deep-nav agent tool.
- Untracked docs/AGENT_HANDOFF.md (was committed before .gitignore; exposed Pi LAN IP). Restored from memory backup after a branch-switch deleted the working copy.

OPEN PRs (all need: merge on green CI + Kilo + my review; CodeRabbit is out of org credits so reviews are rate-limited fake-passes):
- #838 feat(messages): empty states (job 24) on feat/msg-polish-2. Per Jay, BATCH more jobs onto this branch before merging (conserve CodeRabbit reviews). CodeRabbit rate-limited.
- #839 ci: pytest-xdist -n auto. Investigation found the test job was NOT hanging, just slow (~22 min serial, 4845 tests). This parallelizes it. Validate via its own CI run timing, then merge first so the rest merge fast.
- taos-website #1 stats / #2 changelog / #3 nav / #4 accessibility. Combined preview served from Mac tailscale :8899 for Jay; merge after Jay approves.

Decisions (Jay, 2026-06-13): ALWAYS PR for code review (no direct-to-dev). Batch jobs into fewer big PRs (CodeRabbit credits exhausted). Investigate CI slowness (done: #839). Use impeccable + style skills for design work. Widget epic AFTER the job queue.

Next queue (ordered):
1. Land #839 (fast CI), then finish remaining agent jobs BATCHED onto feat/msg-polish-2/#838: 9, 10, 13, 14, 18, 19, 21, 22, 23, 26
2. Bring website PRs #1-4 in after Jay views the preview
3. Light theme (new separate theme; use impeccable skill; theme engine partial in desktop/src/theme)
4. Agent-friendly API: #836 agent tool to dispatch taos:open-app (deep-nav already shipped)
5. Build-widget epic: slim userspace runtime from #476 + My Apps home + agent build tool + share gate
6. #825 key-scope fix; #737 Phase 3 UI (design with Jay)

Pending Jay calls: promote dev->master? enable CodeRabbit add-on (billing) to restore real reviews? store-UI install-path check if model store still errors?

Blockers: theme/userspace need a working session. taos.my Coolify deploy pending Jay.

Security queue: #747 #737 #672 #658 #655 #654 #653 #651 #650 #647

GOTCHA: gh pr merge 401s -- use gh api PUT (squash for sub-PRs, rebase/merge for integration). Admin-merge OK for frontend-only PRs when Python test jobs hang on infra AND spa-build is green. Never --delete-branch on dev->master PR. Jay updates Pi manually.
