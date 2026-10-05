### Added

- Dispatcher lease reconciliation with per-agent exponential back-off (max 6h) and card expiry pause after 5 expiries in 48h.
- `DispatcherService.reconcile_user()` runs before candidate gathering in `tick_user()` and for disabled users to prevent stranded reservations.
- Expired leases un-assign tasks, increment agent back-off, and set `next_eligible_at`.
- Cards with ≥5 expiries in 48h are paused (skipped in assignment) until the window rolls.
- Anti-affinity: re-expired cards prefer a different eligible agent first.