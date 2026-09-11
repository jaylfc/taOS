### Fixed

- The human-principal bus `from` handle is now sanitised (non-printable characters stripped, capped at 64 characters) using the same helper as the admin branch, preventing control-character injection into bus records and logs.
