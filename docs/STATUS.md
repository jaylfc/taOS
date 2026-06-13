SINGLE SOURCE OF TRUTH for cross-agent handoff.
Last updated: 2026-06-13, @taOS (session active, weekly limit reset).

Branch tips: master=99cf786e. dev=3a146015 (ahead of master: full Messages-polish train + handoff untrack).

Session state: ACTIVE. A2A poll monitor armed (seeded id 413). Freshness cron armed (:08/:38, arms resume-pair on next fire). Weekly usage 0%, five-hour ~5%.

Done this session:
- Messages-polish train (jobs 1-7) ALL on dev: #826 markdown, #829 drafts, #830 threads merged direct; #827 emoji, #831 unread, #828 dates (+first-msg-separator fix), #832 search (+mutex fix +2 CodeRabbit fixes) integrated via #833 (conflict cascade). Sub-PRs #827/#828/#831/#832 closed as superseded; branches deleted.
- #783 VERIFIED FIXED on Pi: qwen 2.5 3b + 7b instruct rkllm pull to 100%, load, infer on NPU. strings present, port resolver falls back (rkllama still on legacy 8080). CAVEAT: tested rkllama directly, NOT the store-UI /api/store install route, so if the model-store button still errors, suspect the store->controller/auth path, not rkllama or #783.
- Ideas filed: #796 benchmark pause/resume + hardware-aware queue, #797 native phone, #798 native desktop shared-API, #799 TUI, #834 edit-agent-message-before-send, #835 copy agent text everywhere.
- Untracked docs/AGENT_HANDOFF.md (was committed before .gitignore; exposed Pi LAN IP). File restored from memory backup after a branch-switch deleted the working copy.

Next queue (ordered):
1. (optional, Jay-gated) store-UI install-path check on Pi if the model store still shows errors
2. #825 key-scope fix (LiteLLM routing bug)
3. Theme-package engine (design+plan exist in docs/superpowers)
4. #834 / #835 chat features (edit-before-send, copy-everywhere) -- need short brainstorm
5. Userspace re-land (recon plan from #476 sources/transcript)
6. #737 Phase 3 UI (design session with Jay)

Pending Jay calls: promote dev->master? post #783 live-verify confirm comment (drafted)? scrub Pi IP from handoff git history (low risk, non-routable)?

Blockers: theme/userspace need a working session. taos.my Coolify deploy pending Jay.

Security queue: #747 #737 #672 #658 #655 #654 #653 #651 #650 #647

GOTCHA: gh pr merge 401s -- use gh api PUT (squash for sub-PRs, rebase/merge for integration). Admin-merge OK for frontend-only PRs when Python test jobs hang on infra AND spa-build is green. Never --delete-branch on dev->master PR. Jay updates Pi manually.
