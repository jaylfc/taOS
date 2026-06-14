SINGLE SOURCE OF TRUTH for cross-agent handoff.
Last updated: 2026-06-14 ~04:05 BST, @taOS (active, autonomous overnight).

▶▶ MORNING MUST-DO (Jay overnight ask, asleep): features tested+working by morning; agent OS control DONE simple; **offline agent RESULTS by morning**.
   ** WHEN FEDORA FREES (~05:40Z; A2A monitor watches @taOSmd box-free ping on taOS-taOSmd-hermes-integration; it's running E-009 til then): RUN THE OFFLINE EVAL. cd ~/tinyagentos-private/specs/storybook-demo/ ; follow RUNBOOK.md — score the full roster via storybook_toolcall_eval.py vs the Fedora Ollama (host 100.78.225.80:11434/v1): qwen3.5:9b iq4_xs/q5_k_m/q6_k + qwen3:14b + llama3.1:8b + qwen3:4b + gemma4:12b + qwen3.6:35b-a3b-q4 (+pull qwen3.5:4b). ONE Ollama job at a time (12GB 3060). Produce a tool-call leaderboard. taOSmd pings when free; if this session is dead, the resume cron resumes from here. **

▶ 3 PRs BAKING (merge when green+bots -> ONE Pi deploy -> test ALL light+dark): #878 agent OS control phase-2 (open_app/arrange_windows tools + 09-os-control manual; phase-1 transport #877 ALREADY MERGED), #879 purge-purple-from-dark (cyan swap), #880 mobile-chat polish (avatars+tokens+timestamps, fixes light theme; /chat-pwa = chromeless mobile Messages, mirrors it). VERIFY ON PI: purple gone both themes, /chat-pwa mobile, agent control.

▶ LIVE SESSION STATE (compaction insurance — Jay asked to record often):
- NEW LOOP AUTHORIZED (Jay 2026-06-14 ~02:34): I deploy to the Pi now. Loop = merge to dev when good -> DEPLOY dev to Pi -> TEST on Pi (real screenshots, LIGHT + DARK both) -> more work -> repeat. Reverses the old manual-update rule ([[feedback_taos_update_manual]] updated). PI DEPLOY FACTS: repo /opt/tinyagentos owned by `taos`, service `tinyagentos.service` (User=taos, `python -m tinyagentos`), on branch `dev` (currently behind at 0c367e2c), :6969. jay has sudo (pw in creds), node22/npm present. static/desktop is GITIGNORED -> must rebuild SPA on device. DEPLOY = sudo -u taos git -C /opt/tinyagentos pull origin dev; (cd desktop && sudo -u taos npm ci && npm run build); sudo systemctl restart tinyagentos.service; then VERIFY live (running commit + change actually visible, watch stale PWA cache). Test URL http://192.168.6.123:6969/desktop/ (creds jay/alexander04).
- PR #877 OPEN (feat/agent-os-control-transport) = Phase 1 of the agent-OS-control transport: DesktopCommandBroker (per-user, no-replay, bounded-128 drop-oldest) + GET /api/desktop/stream (SSE) + POST /api/desktop/command + use-desktop-command-stream.ts re-dispatch. 9 backend + 5 frontend tests. BOT FINDINGS FIXED + pushed (1e3bffc2): CRITICAL user-scoping (use request.state.user_id not .user — was a cross-user hole), null-JSON guard, bounded queue, SSE no-cache headers, docs. Re-baking; merge when green + bots clear, THEN deploy+test.
- VERIFIED the screenshot loop works: drove window.taosDesktop on the local preview, tile-3 pixel-correct, single clean titlebars (confirms #25). Pi screenshot = real data but OLD build (needs this deploy). Sent both to Jay.
- STANDING RULES (Jay 2026-06-14): test BOTH light + dark for UI/readability on every screenshot; watch usage window (5h resets 05:40Z, was 46% ~03:35); record tasks/ideas often (compaction).
- OPEN QUESTION from Jay: rename tinyagentos -> taos across modules/services? My rec: YES eventually but it's a BIG risky migration (package imports everywhere, systemd service name, /opt/tinyagentos path, venv, deploy scripts, existing installs) — do as a dedicated staged PR with a compat shim, NOT interleaved with feature work. Captured as idea [[project_taos_rename]] (to write). NOT started.
- TASK #28: verify the dedicated MOBILE chat PWA is redesigned + polished like a mobile Slack client (Messages app: MessagesApp.tsx, AgentMessagesPanel.tsx). Assess + polish; test mobile viewport light+dark on Pi.
- NEXT after #877 merges: deploy+test loop; then Phase 2 (agent MCP tools open_app/arrange + data tools + agent-manual entry, stacked design ready); mobile PWA #28.
- PRIVATE idea captured (gitignored, NEVER public): store paid-apps + optional "taOS tax" 20%/badge -> ~/tinyagentos-private/monetisation-and-pro-relay.md.

▶ NEW NORTH STAR (Jay 2026-06-14 ~01:50): build a promo demo (video) of the taOS agent driving the OS to BUILD A CHILDREN'S STORYBOOK, fully OFFLINE (Pi + Fedora RTX 3060). Flow: summon agent from top bar -> "what shall we work on" -> user: kids story book -> agent opens Projects, names+creates project, types a to-do list live, works the outline -> suggests artwork -> opens image creator, paints onto the ideas board -> chat presents a draft book + cover. Full memory: [[project_storybook_demo]]. RECON DONE: apps mostly REAL (Projects create/Kanban/canvas-with-live-SSE, ImagesApp+generate_image tool, taos_agent chat, top-bar summon). LINCHPIN GAP: agent can't drive the OS (taos:open-app/taos:window only fire in tests; NO controller->browser transport). Offline-model research (2026-06-14): drive via taOS's SEMANTIC MCP tools NOT vision; Qwen3.5-4B/9B (Feb 2026, 256K, MCP) on Fedora 3060 + local SD = cloudless. SPEC NOW WRITTEN (private ~/tinyagentos-private/specs/storybook-demo/AGENT-OS-CONTROL-SPEC.md). KEY INSIGHT: data actions (create_project/add_task/canvas_add_image) ride EXISTING canvas SSE -> ~80% of the visible flow needs NO new transport; only app-open/window-nav need a new DesktopCommandBroker+SSE (mirror project_canvas.py broker; ~30-line frontend re-dispatch to the existing taos:open-app/taos:window receivers). Build seq: (1) transport, (2) nav MCP tools, (3) data MCP tools, (4) forced-local routing+Fedora-as-worker, (5) chat-canvas split-pane [net-new, design pass]. AWAITING JAY: build 1-4 now (low-risk plumbing) or brainstorm UX first? FEDORA EVAL gated on @taOSmd box-free ping (~05:40Z); qwen3.5:9b confirmed local.

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
