---
name: disk-audit
summary: Agent audits its own disk usage and proposes cleanup. User confirms per deletion.
version: 1
required_variables: []
---

You are being asked to audit your own disk usage. Work through this checklist carefully and in order. Do not skip steps.

1. **Establish the current date and time.** Run `date -u +"%Y-%m-%dT%H:%M:%SZ"` and report the result. Every "last modified" / "stale file" decision below must reference this timestamp, not your cached knowledge of when you ran last.

2. **Produce a top-level usage summary.** Run `du -sh /root /var /tmp /opt 2>/dev/null | sort -h` and report the output. Call out the biggest 3 consumers.

3. **Identify cleanup candidates without deleting anything.** For each of these categories, list what's there and the size:
   - npm caches under `~/.npm`, `~/.cache/npm`, `~/.pnpm-store`
   - pip caches under `~/.cache/pip`
   - temp files under `/tmp`, `/var/tmp`
   - build output dirs (node_modules older than 14 days, Python __pycache__, .venv older than 14 days if not currently in use)
   - user projects / workspace folders not modified in 14+ days (use the timestamp from step 1)
   - large files > 100 MB in your home dir — these are candidates for the shared NAS, not deletion

4. **Present a proposed action list.** For each candidate group, propose one of:
   - `[DELETE]` — soft-delete via `rm` (which soft-routes to /var/recycle-bin/ and auto-purges after 30 days; see /docs/runbooks/recycle-bin.md if unfamiliar).
   - `[MOVE-TO-NAS]` — move to the shared NAS under `/nas/archive/<agent-name>/` (requires NAS to be mounted; Phase 4).
   - `[KEEP]` — explicitly chosen to retain (with one-line reason).

5. **Wait for user confirmation.** Do NOT execute any deletions or moves without explicit user approval per item. The user may say "yes to all DELETE, no to MOVE", or go item by item.

6. **Execute only approved actions.** Use `/usr/local/bin/rm` (not `/usr/bin/rm`) so the recycle bin is engaged. Report progress after each action.

7. **Report final state.** Re-run step 2's `du` and show the before/after. Note how much was recovered.

**Reminders**
- Never use `/usr/bin/rm` directly — that bypasses the recycle bin.
- Never `rm -rf /` + a variable without first printing the variable's value for the user to check.
- If a directory you think is stale is actually an active framework cache (.openclaw, .taos), always KEEP regardless of mtime.
