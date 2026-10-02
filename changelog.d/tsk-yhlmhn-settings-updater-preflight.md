### Fixed

Added preflight validation for taOS settings updates to catch common issues before they cause confusing errors:

- Detect when tracked branch is missing from remote origin
- Detect narrow `remote.origin.fetch` refspecs that don't include the tracked branch (auto-repairs when safe)
- Detect files owned by non-service user that would block git operations

When preflight issues are found:
- `check_for_updates` returns HTTP 200 with `preflight_errors` field and `has_updates: false`
- `apply_update` returns HTTP 409 with the preflight errors and does not modify the repository

This ensures updates "just work" for end users without requiring manual troubleshooting.
