### Fixed
- Guard-discrimination gate now reports ERROR (uncheckable) for lru_cache-wrapped targets, default-argument edits, and decorator/default-value mutations instead of incorrectly reporting NON-DISCRIMINATING.
- Mutant IDs for `must_kill` are now stable (kind#ordinal) instead of line-number-based, surviving edits above the guarded function.
- Workflow `paths:` filter removed so the gate can become a required check without stalling PRs.