SINGLE SOURCE OF TRUTH for cross-agent handoff.
Last updated: 2026-06-14 ~03:00 BST, @taOS (active, autonomous overnight).

▶ NEW NORTH STAR (Jay 2026-06-14 ~01:50): build a promo demo (video) of the taOS agent driving the OS to BUILD A CHILDREN'S STORYBOOK, fully OFFLINE (Pi + Fedora RTX 3060). Flow: summon agent from top bar -> "what shall we work on" -> user: kids story book -> agent opens Projects, names+creates project, types a to-do list live, works the outline -> suggests artwork -> opens image creator, paints onto the ideas board -> chat presents a draft book + cover. Full memory: [[project_storybook_demo]]. RECON DONE: apps mostly REAL (Projects create/Kanban/canvas-with-live-SSE, ImagesApp+generate_image tool, taos_agent chat, top-bar summon). LINCHPIN GAP: agent can't drive the OS (taos:open-app/taos:window only fire in tests; NO controller->browser transport). Offline-model research (2026-06-14): drive via taOS's SEMANTIC MCP tools NOT vision; Qwen3.5-4B/9B (Feb 2026, 256K, MCP) on Fedora 3060 + local SD = cloudless. Build path: (1) SSE desktop-command channel [spec first], (2) wire existing funcs as MCP tools (open_app/create_project/add_task/canvas_add_image), (3) chat-canvas split-pane, (4) forced-local routing. AWAITING JAY: whether to spec+build the transport now or brainstorm first.

▶ WAKE QUEUE (order per Jay 2026-06-13 22:20: "real desktop next then live wallpaper work"):
1. REAL DESKTOP #873 OPEN (built + bot findings addressed: delete-confirm, rename sanitise, hidden-icons side-effects, error logs): #22 dock right-click menu, #23 inline New Folder + rename, #24 FS-backed desktop icons + thumbnails + backend POST /api/workspace/rename + tests. Icons/thumbnails/rename need a LIVE Pi check. Merge when tests green.
2. LIVE WALLPAPER: config sliders #21 DONE (density/speed/glow, persisted, in the picker) -- in PR #872. Still need Jay's LIVE look at the tsParticles wallpaper on Pi (headless can't rasterize it); colour + slogan-text editing are the remaining slider follow-ups.
3. STORE redesign #871 MERGED to dev (4 Gitar bugs fixed; hero + Popular real stars/logos + Replace-your-subscriptions + Community, graphite, theme-aware). Pull dev to test.
3b. AGENT OS CONTROLS #18 -- PR #874 OPEN: window.taosDesktop (getLayout + open/move/resize/snap/tile/arrange) + taos:window event; docs/desktop-control.md. Deterministic multi-window screenshots + agent can drive the desktop. Follow-ups: #26 controller agent-tool + manual entry, #25 investigate tiled-window double-header strip.
4. PROMO HERO PROGRAM (memory [[promo-hero-initiative]]): only the agent CHAT + a demo PROJECT stay mock; build everything else REAL. Hero = multi-window (chat + project canvas + store), 5:2 X-cut on all promo. Needs store (#871), project canvas/mind-map (#16, net-new), demo seed (#17), agent window-mgmt API (#18). Mock data PRIVATE on local `marketing` branch (never push/merge; MARKETING.md).
5. Also queued: store popularity LIVE stars backend (#13), per-app install telemetry -> the now-secured stats page (#15), widget redesign (#19, NOT in the shot), mobile audit, wallpaper picker #864, island v2 #854, GitHub #858 ph2, live-wallpaper package brainstorm.

Branch tips: master=6394a3ed. dev=99ab3548 (#873 real desktop + #874 agent OS controls merged). Merged overall this session: #867 #868 #869 #870 (theme/wallpaper), #871 (store redesign), #873 (real desktop: dock right-click + inline New Folder + FS-backed icons + rename API), #874 (window.taosDesktop control API + docs/desktop-control.md); taos-website #5 (stats Basic Auth -> main, set STATS_USER/STATS_PASS in Coolify). Local-only `marketing` branch (private, no upstream; NEVER push/merge).

Session state: ACTIVE (autonomous overnight). ALL baking PRs MERGED to dev (tip=4ecc7961): #872 (tsParticles wallpaper + sliders), #873 (real desktop), #874 (agent OS controls). Open-PR queue drained (only draft #476 remains; #846 already CLOSED). #872 SWAPS the animated wallpaper renderer from the hand-rolled canvas NeuralWallpaper (component "neural") to tsParticles ParticlesWallpaper (component "particles"); theme-store registers id "neural-live" w/ component "particles" -- VERIFY the tsParticles look LIVE on Pi (headless can't rasterize it). #25 (tiled double-header) CLOSED: not a bug, was the 32px top-bar chrome. SECURITY: dependabot alert #5 (esbuild RCE < 0.28.1) is STALE -- desktop already pins esbuild 0.28.1 via overrides (lockfile + installed both 0.28.1); leave for dependabot to auto-close, no code change. #19 widget redesign HELD for Jay (taste + depends on the desktop/widget/dash mode-switcher brainstorm [[project_desktop_modes]]). FEDORA MODEL TESTS (Jay 2026-06-14 ~02:00): eval harness + runbook built PRIVATE (~/tinyagentos-private/specs/storybook-demo/storybook_toolcall_eval.py) -- scores local models on the storybook tool-call flow incl ID-threading; A2A sent to @taOSmd (msg 431) to coordinate Fedora box (it's mid E-009 sweep, do NOT interrupt); awaiting its ping + local-model list. tsParticles look + Safari dark<->light + live-wallpaper animation + desktop icons/thumbnails are all best checked LIVE on the Pi (preview has no backend; tsParticles canvas does not rasterize headless).

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
