### Added

- `taos reset --onboarding` clears the account store, onboarding SQLite stores, and setup-checklist preference so onboarding can be re-run without creating duplicate users. A timestamped backup is saved under `data/backups/reset-<UTC ISO>/` by default. `taos reset --all` extends this to all mutable state in `data/` while preserving downloaded models (`data/models`) and installed apps (`data/apps`).
