### Fixed
- Catalog manifest audit now also scans `install.companions[].env` for literal secrets, closing a hole where companion env vars like `POSTGRES_PASSWORD` could ship with hardcoded values unflagged.
