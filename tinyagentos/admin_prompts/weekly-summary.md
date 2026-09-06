---
name: weekly-summary
summary: Agent reviews its own trace history for the past 7 days and produces a prioritised summary of tasks, token usage, cost, and notable outcomes.
version: 1
required_variables: []
---

You are being asked to produce a weekly self-report. This is a read-only task. Work through each section in order.

1. **Establish the current date and time.** Run `date -u +"%Y-%m-%dT%H:%M:%SZ"` and record it. Compute the ISO 8601 timestamp for exactly 7 days ago — you will use this as the `since` parameter.

2. **Fetch your trace history.** Call `GET /api/agents/{self}/trace?since=<7d-ago>&limit=500` where `{self}` is your own agent name and `<7d-ago>` is the timestamp from step 1. If the endpoint returns a non-200 status, report the error and note that the summary is based on partial data.

3. **Summarise task volume.** From the trace records, count and report:
   - Total number of tool calls / completions
   - Breakdown by day (a simple table: Date | Calls)
   - Top 3 most-used tools or action types

4. **Token and cost summary.** Aggregate from the trace:
   - Total prompt tokens and completion tokens
   - Total estimated cost (use the per-token rates in the trace records if present; otherwise note "cost data unavailable")
   - Average tokens per task

5. **Notable successes.** List up to 5 tasks that completed with status "success" and had the highest token spend or were flagged as high-priority. One line each: task ID, brief description, outcome.

6. **Notable failures or errors.** List up to 5 tasks that errored, timed out, or were retried more than twice. For each: task ID, error summary, whether it was eventually resolved.

7. **What the user should know.** Write 3–5 bullet points covering:
   - Any recurring failure pattern that needs attention
   - Any unusually high-cost tasks (> 2x average cost)
   - Any capability gaps encountered (e.g. tool not available, model refused a request)
   - Positive outcomes worth highlighting

8. **Closing statement.** One sentence: "This report covers <N> trace records from <start> to <end>."

**Reminders**
- Do not modify any trace records or agent configuration.
- If no trace records are returned, say so clearly and skip sections 3–7.
- Round all costs to 4 decimal places and all token counts to the nearest whole number.
