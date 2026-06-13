SINGLE SOURCE OF TRUTH for cross-agent handoff.
Last updated: 2026-06-13 ~23:40 BST, @taOS (WIND-DOWN at 5h usage 93%).

▶ WAKE QUEUE (resume after the ~01:40 BST 5h reset; resume pair armed 01:43/02:02). ORDER per Jay 2026-06-13 22:20: "real desktop next then live wallpaper work":
1. REAL DESKTOP feature set (Jay's stated NEXT priority): #22 dock right-click menu (min/max/close/quit/keep-in-dock); #23 New Folder = inline untitled folder + instant rename in the real Desktop dir (NOT open Files -- current bug); #24 desktop renders the real profile Desktop folder as icons + thumbnails/previews (FS-backed via Files/FS API, open/rename/delete/drag). One "real desktop" feature set, mock-first.
2. LIVE WALLPAPER work (after real desktop): verify tsParticles #872 LOOK live on Pi (headless can't rasterize it), then build the config sliders (#21: speed/density/glow/colour/text) -- the user/agent-authorable foundation (tsParticles engine).
3. STORE redesign #871 OPEN -- @taOS to FIX 4 Gitar bugs (Jay said do it myself), then merge: (a) search doesn't filter Discover/Community views, (b) several app types unreachable from sidebar nav (NAV_TYPE_MAP incomplete), (c) "Updates" nav identical to "Installed", (d) RichCard install target captured at mount, ignores device selection. Redesign = hero + Popular (real GitHub stars + dashboard-icons logos) + Replace-your-subscriptions homelab + Community + frameworks, graphite, theme-aware.
4. PROMO HERO PROGRAM (memory [[promo-hero-initiative]]): only the agent CHAT + a demo PROJECT stay mock; build everything else REAL. Hero = multi-window (chat + project canvas + store), 5:2 X-cut on all promo. Needs store (#871), project canvas/mind-map (#16, net-new), demo seed (#17), agent window-mgmt API (#18). Mock data PRIVATE on local `marketing` branch (never push/merge; MARKETING.md).
5. Also queued: store popularity LIVE stars backend (#13), per-app install telemetry -> the now-secured stats page (#15), widget redesign (#19, NOT in the shot), mobile audit, wallpaper picker #864, island v2 #854, GitHub #858 ph2, live-wallpaper package brainstorm.

Branch tips: master=6394a3ed. dev=4f2259a3. Merged this session: #867 #868 (theme+neural), #869 (popover/widget graphite colours), #870 (graphite static wallpaper + light/dark inversion); taos-website #5 (stats Basic Auth -> main, set STATS_USER/STATS_PASS in Coolify). Local-only `marketing` branch (private, no upstream; NEVER push/merge).

Session state: WIND-DOWN at 5h 93%. OPEN PRs: #871 (store, needs the 4 bug fixes), #872 (tsParticles wallpaper, verify live). @taOS's next action on resume = fix #871's 4 bugs. tsParticles + Safari dark<->light + neural/live wallpaper animation are all invisible to screenshots -> Jay verifies live on the Pi.

WEBSITE: taos.my live. All 4 taos-website PRs merged (stats/changelog/nav/accessibility).

CI: test suite parallelized via #839 (xdist -n auto). CodeRabbit may be out of credits -- do not merge on a fake rate-limit pass. Use @coderabbitai full review to retrigger; manual review OK for tiny already-reviewed PRs.

OPEN PRs:
- #872 feat(wallpaper): tsParticles live "Neural (Live)" wallpaper (selectable, theme-aware) -- verify look live, then sliders (#21)
- #871 feat(store): App Store redesign -- has 4 Gitar bugs for @taOS to fix before merge (see WAKE QUEUE 3)
- #846 dependabot esbuild bump -- SUPERSEDED by #849 (already on dev); close it
- #476 DRAFT feat(userspace): App Runtime v1 -- stays DRAFT, not ready to merge
(merged to dev this session: #867 #868 #869 #870 theme/wallpaper/popovers/inversion. taos-website #5 stats-auth -> main.)

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
