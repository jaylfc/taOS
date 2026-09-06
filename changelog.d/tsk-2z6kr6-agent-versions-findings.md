### Fixed
- `InvalidContainerTargetError` (malformed agent name or remote) is now caught in all agent version routes, returning 400 instead of 500.
- Agent state revert now uses dedicated `DirtyTreeError` and `NotAncestorError` exceptions instead of string matching.
- `git rev-parse HEAD` return code is now checked in `git_revert`.
- Agent version routes now enforce owner-or-admin authorization on list, diff, and revert operations.
- Cross-process lock serializes state writers (committer and revert) to prevent lost commits.
- Committer startup failures are now reported as `committer_failed` steps.
