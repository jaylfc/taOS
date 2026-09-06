---
name: health-report
summary: Agent produces a read-only status report covering systemd units, recent errors, open file descriptors, and network reachability.
version: 1
required_variables: []
---

You are being asked to produce a health report. This is a read-only task — do not modify any configuration or restart any service. Work through each section in order.

1. **Establish the current date and time.** Run `date -u +"%Y-%m-%dT%H:%M:%SZ"` and include it at the top of your report.

2. **Systemd unit status.** Run `systemctl list-units --state=failed 2>/dev/null` and report any failed units. If none, say "No failed units." Then run `systemctl status openclaw 2>/dev/null || true` and report whether the framework service is active.

3. **Recent error log lines.** Run `journalctl -p err -n 30 --no-pager 2>/dev/null` and report the last 30 error-level entries. Group by unit name and summarise repeated patterns (e.g. "5 occurrences of X in unit Y").

4. **Open file descriptors.** Run `ls /proc/self/fd 2>/dev/null | wc -l` to count your own open FDs. Then run `lsof -p $$ 2>/dev/null | tail -20` for the top 20 by recency. Flag if FD count is above 256.

5. **Network reachability.** Test the following endpoints and report HTTP status code or connection error for each:
   - LiteLLM proxy: `curl -sf -o /dev/null -w "%{http_code}" http://localhost:4000/health 2>/dev/null || echo "unreachable"`
   - taOS controller: `curl -sf -o /dev/null -w "%{http_code}" http://localhost:6969/api/health 2>/dev/null || echo "unreachable"`
   - DNS resolution: `nslookup example.com 2>/dev/null | head -4 || echo "DNS unavailable"`

6. **Summary paragraph.** Write a 3–5 sentence plain-language summary of overall health. Use this structure: "As of <timestamp>, this agent is in <good/degraded/critical> health. [Key issues]. [Notable observations]. [Recommended next steps, if any]."

7. **Status table.** Produce a markdown table with columns: Check | Status | Detail. One row per check above (systemd, error logs, FDs, LiteLLM, taOS, DNS).

**Reminders**
- This is a report only. Do not restart services, clear logs, or change any settings.
- If a command is unavailable (e.g. journalctl not present), note "unavailable" and continue.
