- `bot-review-gate`: the head-SHA reconciler now fails closed when it cannot
  update a stale check run. Previously a failed PATCH (network error or a
  non-2xx refusal) was treated as a no-op, so the reconciler published a fresh
  passing run alongside the stale FAILURE it had not managed to update. Because
  `mergeStateStatus` keys off ANY failing run, that left the PR pinned on
  UNSTABLE while the reconcile reported a successful write.
- `bot-review-gate`: the workflow's `--head-sha` wiring and the multi-page
  check-run aggregation are now covered by tests; both were previously
  unasserted, so either could regress with the suite fully green.
