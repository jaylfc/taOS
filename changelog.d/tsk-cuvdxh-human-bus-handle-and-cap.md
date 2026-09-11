### Fixed

- `check_agent_project_grants` now raises 403 with detail `"human-principal token cannot enumerate agent project grants"` instead of returning the no-credential sentinel `(None, {})`, so a valid human token on the project-tasks path is distinguishable from a missing Authorization header.
- The human-principal bus `from` handle now falls back to `"@operator"` when sanitisation would otherwise yield a bare `"@"`, preventing two users with all-non-printable usernames from colliding.
- Usernames are capped at 63 characters before the `@` prefix is added, so `@` + username never exceeds the 64-character handle cap and no truncation collision can occur.
