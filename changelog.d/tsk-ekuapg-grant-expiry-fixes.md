### Fixed
- Re-approving an expired grant with a bounded `expires_at` now restores access instead of silently keeping the dead bound; an unbounded re-approval preserves the expired row rather than widening it to NULL.
- `add_grant` now compares `expires_at` instants via `datetime.fromisoformat` instead of string `min`, so cross-timezone bounds are evaluated correctly.
- `renew=True` through `add_agent_to_project` now honours the caller's `expires_at` even when a deferred grant with an earlier expiry exists.
- `AgentScopeRequestsStore.create` now persists `duration_secs`, so `approve_scope_request` can derive `expires_at` from the request.
