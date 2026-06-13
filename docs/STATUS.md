SINGLE SOURCE OF TRUTH for cross-agent handoff.
Last updated: 2026-06-13, @taOS (freshness sweep).

Branch tips: master=99cf786e. dev=960d5464 (27 ahead of master: Messages train + activity fix + deep-nav API + agent jobs 8/12/16/17).

Session state: ACTIVE. A2A poll monitor ARMED (seeded id 413). Freshness cron :08/:38 ARMED. 5h resume-pair ARMED (primary 15:53, retry 16:12 local; session-scoped). 5h usage 47%, weekly 5%.

Done (since last STATUS.md update, 2026-06-13):
- Messages-polish train (jobs 1-7) ALL on dev via #826/#829/#830 direct + #833 integration (#827/#828/#831/#832); sub-PRs closed superseded, branches deleted.
- #783 VERIFIED FIXED on Pi: qwen 2.5 3b + 7b instruct rkllm pull to 100%, load, infer on NPU. CAVEAT: tested rkllama directly, NOT the store-UI /api/store install route.
- fix(activity): dedupe local node in scheduler + detect ARM SoC (RK3588) for CPU label (c55f9292, 4 tests). Pi shows it after next deploy (hardware profile cached).
- feat(desktop): deep-navigation API (?app= url + taos:open-app event), extracted to tested useDeepNavigation hook (14 tests). Tracked #836.
- Agent jobs done direct-to-dev: 8 Cmd+K switcher, 12 theme inventory (#837 merged), 16 CodeBlock tests, 17 update-ping toggle.
- Ideas filed: #796 benchmark pause/resume, #797 native phone, #798 native desktop shared-API, #799 TUI, #834 edit-before-send, #835 copy agent text, #836 deep-nav agent tool.
- Untracked docs/AGENT_HANDOFF.md (was committed before .gitignore; exposed Pi LAN IP). Restored from memory backup after a branch-switch deleted the working copy.

In flight: background agent doing website jobs 11/15/20/25 (repo jaylfc/taos-website, serial since they share index.html). On completion: integrate locally, serve, send Jay a Tailscale preview link.

Next queue (ordered):
1. Finish remaining agent jobs (MessagesApp serial chain + tests/manual): 9, 10, 13, 14, 18, 19, 21, 22, 23, 24, 26
2. Then (Jay-confirmed) the agent-builds-a-widget epic: slim userspace runtime from #476 + My Apps home + agent build tool + share gate
3. #825 key-scope fix (LiteLLM routing bug)
4. Theme-package engine (design+plan in docs/superpowers; inventory #837 says inline styles in WidgetLayer.tsx + MessagesApp.tsx are the real work)
5. Userspace re-land (recon from #476 sources)
6. #737 Phase 3 UI (design session with Jay)

Pending Jay calls: promote dev->master (26 ahead)? store-UI install-path check if model store still errors?

Blockers: theme/userspace need a working session. taos.my Coolify deploy pending Jay.

Security queue: #747 #737 #672 #658 #655 #654 #653 #651 #650 #647

GOTCHA: gh pr merge 401s -- use gh api PUT (squash for sub-PRs, rebase/merge for integration). Admin-merge OK for frontend-only PRs when Python test jobs hang on infra AND spa-build is green. Never --delete-branch on dev->master PR. Jay updates Pi manually.
