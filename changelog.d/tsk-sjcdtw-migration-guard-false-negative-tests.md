### Added
- Tests for expression-index, quoted-identifier, and case-mismatch false negatives in both migration guards
- Case-insensitive table/column matching in `check_schema_migrations.py` and `check_retrofit_migrations.py`
- sqlglot-based SQL parsing in `check_retrofit_migrations.py` for quoted-identifier and case-insensitive robustness
