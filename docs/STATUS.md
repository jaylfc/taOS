SINGLE SOURCE OF TRUTH for cross-agent handoff.
Last updated: 2026-06-13 ~18:46, @taOS (wind-down at 5h usage 92%).

▶ WAKE QUEUE (resume here after the 19:40 UTC 5h reset, Jay's active queue):
1. THEME: move taOS Dark off the blue-purple base (#1a1b2e) to macOS dark-mode NEUTRALS (#865). Edit dark @theme tokens in desktop/src/theme/tokens.css + retune WALLPAPERS off indigo; accent stays neutral grey; keep Light theme; verify contrast.
2. MOBILE AUDIT: check the Agents app + chat/Messages + composer look right on mobile (Jay flagged).
3. WALLPAPER picker Phase 1 (#864): reorg into Theme-default / Built-in / Your-wallpapers(+upload) sections + a Settings entry point; Phase 2 = Wallhaven KEYLESS browse + optional API-key entry. Mock approved.
4. DYNAMIC ISLAND v2 (#854): design+mocks approved (island holds agent+search, agent chat bubble replaces side panel + poppable window, search bubble, Mac animations). Build plan then build.
5. GITHUB (#858): Phase 1 connect flow MERGED (#862). Next: Phase 2 time-scoped sharing + consent picker; Phase 3 agent access-request + runtime token injection; Phase 4 fork->PR ops. OAuth app registered (Client ID Ov23licVGSIqagQLXAqb public/in-source; secret stays host-side, NOT in repo; device flow needs no secret).

Branch tips: master=6394a3ed. dev advanced well past #845: merged this session = #843 #847 #848 #849 #850 #851 #852 #853 #857 #859 #860 #861 #862. OPEN PR baking: #863 (agents spacing + System-agent-label removal; merge on green).

Session state: wind-down. Merge #863 when green. Resume the WAKE QUEUE after reset.

WEBSITE: taos.my live. All 4 taos-website PRs merged (stats/changelog/nav/accessibility).

CI: test suite parallelized via #839 (xdist -n auto). CodeRabbit may be out of credits -- do not merge on a fake rate-limit pass. Use @coderabbitai full review to retrigger; manual review OK for tiny already-reviewed PRs.

OPEN PRs:
- #860 feat(theme): theme chooser shows only taOS Dark + taOS Light (remove Matrix Terminal) -- feat/themes-taos-light-dark
- #859 fix(agents): kill switch surfaces failures -- Gitar review follow-up on #857, fix/kill-switch-error-handling
- #846 dependabot esbuild bump -- SUPERSEDED by #849 (already on dev); close it
- #476 DRAFT feat(userspace): App Runtime v1 -- stays DRAFT, not ready to merge

Notable open issues (bugs first):
- #844 rkllama store-UI install chain broken (wrong script + non-interactive false-success) -- unresolved
- #841 update check shows no updates when local branch diverged from origin -- unresolved
- #825 taOS agent model swap breaks routing (stale per-agent key preferred over master key)
- #840 chat: per-agent framework slash commands (Telegram-style) in DMs and via @agent /
- #836 deep-navigation API for taOS agent to drive desktop (hook shipped; agent tool side pending)

Done (since last STATUS.md update):
- ALL 26 agent jobs COMPLETE and on master (via #845 batch).
- Messages-polish (#838), agent manual templates (#842), CI parallelization (#839) all merged to dev then master.
- Light theme (#848), esbuild RCE patch (#849), brand rename (#847), chat composer unified (#850), Agents redesign (#851), update flow fix (#852), Chat Slack-polish (#853), agent kill switch (#857) all on dev.
- This sweep: docs/STATUS.md (dev tip + #860), docs/agent-qmd-serve-setup.md + docs/mirror-policy.md (brand rename TinyAgentOS->taOS).

Next queue:
1. Land #859 and #860 after CI + review.
2. Close #846 (superseded by #849).
3. Fix #844 and #841 (bugs, high user impact).
4. #825 key-scope fix.
5. Desktop overhaul (#824) and widget epic: needs Jay design session first.

Decisions (carried from prior sessions):
- PR for all code changes (no direct-to-dev commits for features).
- Never --delete-branch on a dev->master PR (deletes dev, closes all dev-targeting PRs).
- Jay updates Pi manually -- do not SSH-deploy after merges.
- gh pr merge 401s -- use GitHub UI or gh api PUT for merges.

Security queue: #747 #737 #672 #658 #655 #654 #653 #651 #650 #647

GOTCHA: docs/AGENT_HANDOFF.md is intentionally untracked (exposed Pi LAN IP in a prior commit; restored from memory but kept out of git). The RESTART CHECK at its top is stale (referenced #752, long-closed); ignore it.
