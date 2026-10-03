### Fixed

- Added preflight validation for taOS settings updates to catch common issues before they cause confusing errors:
  - Detect when tracked branch is missing from remote origin
  - Detect narrow `remote.origin.fetch` refspecs that don't include the tracked branch
  - Detect files not writable by the service user that would block git operations
- When preflight issues are found:
  - `check_for_updates` returns HTTP 200 with `preflight_errors` field and `has_updates: false`
  - `apply_update` returns HTTP 409 with the preflight errors and does not modify the repository
- Root installs (install.sh User=root) can now update: preflight uses a writability test instead of ownership, so root is never flagged.
- Branch validation uses `git check-ref-format` instead of `str.isalnum`, so names like "release-1.0" and "feature/x" are accepted.
- Unreachable origin (ls-remote nonzero exit) no longer blocks as branch_not_on_origin; the existing fetch step surfaces that error.
- `check_preflight` is offloaded to a thread via `asyncio.to_thread` so it does not block the server event loop.
