### Fixed
- Agent state versions API now parses auto-commit subjects that contain `|` by using a non-printable delimiter instead of the pipe character.
- Reverting to the current HEAD returns 200 `{"status": "noop"}` instead of 404; non-ancestor SHAs return 409, and dirty working trees return 409.
- SHA validation tightened to require at least 7 hex characters.
- SSH key files under `.ssh/` are excluded from agent state history via `.gitignore`.
- Deploy results now surface `versioning: false` and `versioning_error` when the state-repository setup fails.
- The auto-committer is installed as a systemd unit with `Restart=always` so it survives container reboots; nohup remains the fallback.
- The committer script now uses `git diff --name-only` for its change summary, discarding the fragile footer heuristic.
- The committer script checks Git command return codes and logs errors to stderr instead of silently swallowing them.