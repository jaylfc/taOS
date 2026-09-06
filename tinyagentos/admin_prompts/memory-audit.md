---
name: memory-audit
summary: Agent inspects its own process and cache memory usage and proposes OOM-safe cleanups. User confirms per action.
version: 1
required_variables: []
---

You are being asked to audit your own in-container memory usage. Work through this checklist in order. Do not skip steps.

1. **Establish the current date and time.** Run `date -u +"%Y-%m-%dT%H:%M:%SZ"` and report the result. All "idle since" decisions must reference this timestamp.

2. **Report current memory pressure.** Run `free -m` and report total, used, free, and available. Flag immediately if available RAM is below 128 MB.

3. **List top memory consumers by RSS.** Run `ps aux --sort=-%mem | head -20` and report the top 10 processes. Note any process using > 200 MB RSS that is not a known system service.

4. **Identify reclaimable caches.** Check and report sizes for:
   - Linux page cache and slab: `cat /proc/meminfo | grep -E "Cached:|SReclaimable:|Slab:"`
   - Python in-process module caches: not directly measurable; note if any Python processes are idle for > 1 hour (use timestamp from step 1)
   - Node.js / npm worker processes that are idle (no activity in the process table for > 30 min)
   - tmpfs mounts: `df -h | grep tmpfs`

5. **Present a proposed action list.** For each candidate, propose one of:
   - `[DROP-CACHE]` — request Linux to drop page/slab caches via `echo 3 > /proc/sys/vm/drop_caches` (safe on idle system; disrupts performance briefly on active one).
   - `[KILL-IDLE]` — send SIGTERM to an idle process (print PID, name, and RSS before proposing).
   - `[KEEP]` — retain with a one-line reason.

6. **Wait for user confirmation.** Do NOT issue any kill signals or drop any caches without explicit user approval per item.

7. **Execute only approved actions.** Report the PID and outcome for each kill. For cache drops, re-run `free -m` and report the delta.

8. **Report final state.** Show `free -m` before/after and summarise total memory recovered.

**Reminders**
- Never kill PID 1 or any process with PPID 1 without explicit instruction.
- If available RAM was already above 512 MB at step 2, recommend KEEP for all items unless the user wants to proceed anyway.
- Do not touch processes owned by root if you are not running as root — propose only, let the user decide.
