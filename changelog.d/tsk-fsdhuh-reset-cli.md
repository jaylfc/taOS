### Added

- `taos reset --onboarding` clears identity, auth, and onboarding state so onboarding can be re-run without creating duplicate users. A timestamped backup is created under `data_dir/backups/` by default.
