### Fixed
- Restore `_split_columns` helper in `scripts/check_schema_migrations.py` that was dropped by the #2885 rewrite; the regex fallback path once again correctly splits multi-column CREATE TABLE bodies, including quoted column identifiers.
