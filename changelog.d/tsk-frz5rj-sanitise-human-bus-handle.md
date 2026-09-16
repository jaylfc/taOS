### Fixed

- The human-principal bus `from` handle is now sanitised (non-printable characters stripped, capped at 64 characters) using the same helper as the admin branch, preventing control-character injection into bus records and logs.
- A valid human-principal token on the project-tasks aggregate now returns a distinguishable 403 instead of being conflated with the no-Authorization-header sentinel, preventing a silent empty 200.
- The human-principal bus handle now falls back to `@<user_id>` when the username is empty or entirely non-printable after sanitisation, preventing two distinct users from collapsing to the bare `@` handle.
