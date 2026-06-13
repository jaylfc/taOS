SINGLE SOURCE OF TRUTH for cross-agent handoff.
Last updated: 2026-06-13, @taOS freshness sweep (cycle 3).

Branch tips: master=99cf786e (PR #813 batch). dev=5ebb6995 (6 docs commits ahead of master, pre-sweep).

Open PRs: #826-#832 Messages train (7 PRs, awaiting review). #476 App Runtime v1 DRAFT.

Next queue (ordered):
1. Review + merge Messages train (#826-#832)
2. #825 key-scope fix (LiteLLM routing bug)
3. Userspace re-land (recon plan from transcript first)
4. #737 Phase 3 UI (design session with Jay)
5. #747 CSRF extend verify_csrf

Recently merged: #818 #817 #816 #812 #811 #809 #808 (all on master 99cf786e).

Blockers: #737 Ph3 needs design session. Userspace plan lost, recon needed. taos.my Coolify pending Jay.

Security queue: #747 #737 #672 #658 #655 #654 #653 #651 #650 #647

GOTCHA: gh pr merge 401s -- use gh api PUT instead. Never --delete-branch on dev->master PR. Jay updates Pi manually.
