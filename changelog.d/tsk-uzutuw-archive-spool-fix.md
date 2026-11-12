### Fixed

- archive_spool: removed stale `.done` index that caused silent data loss when a new spool was created after a crash; drain now replays `.draining` in full (at-least-once delivery)
- archive_spool: failed/malformed lines are now appended back to spool.jsonl immediately during drain (bounded memory); if append-back fails, loop stops and `.draining` is restored for retry
- archive_spool: added `_spool_restore` to restore tagged values (bytes, datetime, Path) on replay instead of leaving them as tagged dicts
- archive_spool: moved blocking file I/O off the event loop via `asyncio.to_thread` in both `record_lossless` and `drain_spool`
- archive_spool: `_append_spool_line` now closes directory fd in try/finally; critical logs no longer include event content
