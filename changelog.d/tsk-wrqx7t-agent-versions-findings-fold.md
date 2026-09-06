### Fixed
- `git_log` now places the commit message as the last field so filenames containing the field separator cannot corrupt parsing.
- Committer install now runs `mkdir -p /root/.taos` before pushing the script, and disables versioning when directory creation fails.
- Agent version routes now fail closed with 403 when ownership cannot be resolved from the registry or agent config.
- `git_rev_parse` and `git_diff` now distinguish container execution failures from unknown revisions, returning 409 `container_unreachable` instead of 404 when the container is unavailable.
- `git_rev_parse` and `git_diff` now classify unknown revisions from git's actual diagnostics ("needed a single revision", "unknown revision", "bad revision", "bad object", matched case-insensitively) instead of a single guessed phrase that git never emits for these commands.
- The agent state `.gitignore` now also excludes `.env.*` variants (e.g. `.env.local`, `.env.production`), not just the exact `.env` filename, so they can no longer be committed and exposed through the version-diff route.
- Agent state revert now decides "noop vs. reverted" atomically, inside the container's state lock, instead of comparing HEAD before the lock is taken — a committer creating a new commit in that window could previously make the route falsely report `noop` without actually reverting.
- Deploy now reports `versioning=False` with a `versioning_error` when the committer script is missing, fails to push, or fails to launch via nohup, matching the existing behavior for other committer install failures.
