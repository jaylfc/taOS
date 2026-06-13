SINGLE SOURCE OF TRUTH for cross-agent handoff.
Last updated: 2026-06-13 ~21:25 BST, @taOS (active, 5h usage 21% post-reset).

▶ WAKE QUEUE (Jay's active queue, resumed post-reset):
1. THEME #865 DONE-IN-PR: taOS Dark moved off blue-indigo (#1a1b2e) to macOS graphite NEUTRALS (#1d1d1f / #171717), neutral frosted chrome, accent grey kept, Light theme untouched. Shipped with a LIVE adaptive neural wallpaper (canvas, any aspect ratio incl 3840x1200 ultrawide; optional taOS wordmark toggle). PR #868. Also Safari backdrop-filter repaint fix = PR #867. Both need Jay's live Pi visual check (animation + Safari dark<->light are invisible to screenshots). Merge each when bot-review green.
2. BRAINSTORM (next-up, Jay's call): live-wallpaper PACKAGE format + agent authoring guidelines + store sharing. Prototype proven (neural graphite). See memory [[live-wallpapers]]. Brainstorm after Jay confirms #868 live.
3. MOBILE AUDIT: check the Agents app + chat/Messages + composer look right on mobile (Jay flagged); verify the new neural wallpaper + graphite chrome on mobile too.
4. WALLPAPER picker Phase 1 (#864): reorg into Theme-default / Built-in / Your-wallpapers(+upload) sections + a Settings entry point; Phase 2 = Wallhaven KEYLESS browse + optional API-key entry. Mock approved. NOTE: #868 already added a wallpaper "kind" + wordmark toggle to the picker; build on that.
5. DYNAMIC ISLAND v2 (#854): design+mocks approved (island holds agent+search, agent chat bubble replaces side panel + poppable window, search bubble, Mac animations). Build plan then build.
6. GITHUB (#858): Phase 1 connect flow MERGED (#862). Next: Phase 2 time-scoped sharing + consent picker; Phase 3 agent access-request + runtime token injection; Phase 4 fork->PR ops. OAuth app registered (Client ID Ov23licVGSIqagQLXAqb public/in-source; secret stays host-side, NOT in repo; device flow needs no secret).

Branch tips: master=6394a3ed. dev=7f14fc88 (#863 merged). OPEN PRs baking (merge on green): #867 (Safari backdrop-filter repaint), #868 (macOS-dark graphite + live neural wallpaper, #865).

Session state: ACTIVE (resumed post-reset, 5h 21%). PRs #867 + #868 open for review; resume the WAKE QUEUE.

WEBSITE: taos.my live. All 4 taos-website PRs merged (stats/changelog/nav/accessibility).

CI: test suite parallelized via #839 (xdist -n auto). CodeRabbit may be out of credits -- do not merge on a fake rate-limit pass. Use @coderabbitai full review to retrigger; manual review OK for tiny already-reviewed PRs.

OPEN PRs:
- #868 feat(theme): macOS-dark graphite palette + live neural wallpaper (#865) -- feat/macos-dark-theme; merge on green; needs Jay live Pi check
- #867 fix(theme): repaint backdrop-filter layers on theme switch (Safari blacked-out) -- fix/safari-theme-repaint; merge on green; Safari-only, verify live
- #846 dependabot esbuild bump -- SUPERSEDED by #849 (already on dev); close it
- #476 DRAFT feat(userspace): App Runtime v1 -- stays DRAFT, not ready to merge
(merged since last update: #859 kill-switch errors, #860 theme chooser taOS Dark+Light, #863 agents spacing)

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
