### Fixed

- Anthropic streaming through the LLM gateway now records the trace and spend when the client aborts or the upstream stream errors mid-way, before the stream close returns instead of whenever garbage collection runs. An unfinished stream is charged real usage once a `message_delta` usage arrived, else a conservative estimate marked `usage_estimated`, and is traced as `failure`. Client aborts and cancellation are no longer swallowed, so aborting a stream can no longer dodge the spend cap.
- The gateway's stream failover loop now closes each backend attempt explicitly when its own consumer closes it.
