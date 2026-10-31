### Fixed

- Restored the `test_other_owner_cannot_hijack_active_grant` regression test on the project artifact kind, covering the fail-closed `_caller_owns_artifact` route-level check that prevents a non-owner from hijacking an active grant. Replaced a silent `if` guard in `test_sharing_routes.py` with an `assert` so app-consent setup can never be skipped.
