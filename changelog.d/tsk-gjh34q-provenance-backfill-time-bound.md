### Fixed

- Time-bounded provenance backfill now stamps all pre-cutoff gate decisions regardless of their status (pending/answered/superseded), matching the convergence requirements for pre-#2748 rows.

The backfill now records a single cutoff time on first run and only stamps decisions created strictly before that timestamp, ensuring idempotency across restarts. This prevents the caller-minted-privileges hole by not re-stamping decisions created after the deploy cutoff.

**Caveat:** Decisions with status='answered' or 'superseded' are now stamped if created before cutoff. This preserves the original intent of the tsk-mul5pa upgrade backfill (which had this limitation), while satisfying the convergence requirement by using a time bound instead of status.

**Files changed:** tinyagentos/decisions/decision_store.py