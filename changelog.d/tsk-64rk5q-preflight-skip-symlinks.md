### Fixed

- Preflight no longer flags dangling or stale symlinks as foreign-owned, so a single symlink in the install dir no longer blocks every Settings update with a 409 foreign_owned_files.