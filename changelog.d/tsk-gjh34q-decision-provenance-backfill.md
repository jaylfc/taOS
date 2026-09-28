### Fixed
- Decision provenance backfill is now time-bounded and idempotent: a recorded cutoff prevents restamping post-deploy rows, and pre-cutoff gate decisions are classified as server-raised exactly once.
