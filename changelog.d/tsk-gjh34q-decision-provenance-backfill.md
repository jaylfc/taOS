### Fixed
- Decision provenance backfill now stamps ALL pre-cutoff gate decisions regardless of status (previously excluded answered/superseded rows via `status = 'pending'` filter), making the one-shot migration convergent and idempotent across restarts.
