SINGLE SOURCE OF TRUTH for cross-agent handoff.
Last updated: 2026-06-13, @taOS (freshness sweep).

Branch tips: master=6394a3ed (PR #845 batch). dev=cb0ee722 (8 ahead of master). On dev since #845: rkllama install fix (#843), brand rename to taOS + contact info@taos.my (#847), Light theme (#848), esbuild 0.28.1 RCE fix (#849), unified chat composer (#850), Agents app Apple-card redesign (#851), update reset-before-pull fix (#852), Chat Slack-polish (#853).

Session state: freshness sweep only. No active session crons armed this session.

WEBSITE: taos.my live. All 4 taos-website PRs merged (stats/changelog/nav/accessibility).

CI: test suite parallelized via #839 (xdist -n auto). CodeRabbit may be out of credits -- do not merge on a fake rate-limit pass. Use @coderabbitai full review to retrigger; manual review OK for tiny already-reviewed PRs.

OPEN PRs:
- #857 feat(agents): top-bar agent kill switch (feat/agent-kill-switch) -- in review
- #846 dependabot esbuild bump -- LIKELY SUPERSEDED by #849 (already on dev); verify and close if so
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
- Light theme (#848), esbuild RCE patch (#849), brand rename (#847), chat composer unified (#850), Agents redesign (#851), update flow fix (#852), Chat Slack-polish (#853) all on dev.

Next queue:
1. Verify and close #846 (superseded by #849).
2. Land #857 (kill switch) after CI + review.
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
