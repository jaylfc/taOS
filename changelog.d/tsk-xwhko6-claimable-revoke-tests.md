### Added

- Acceptance tests for the claimable revoke fix: verify that revoking a task strips both `claimable` and `fleet:claimable` labels, including the `fleet:claimable`-only case, and that granting still adds only `claimable`.
