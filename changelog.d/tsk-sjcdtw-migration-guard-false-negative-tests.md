### Added
- False-negative regression tests for both migration guards: expression indexes, quoted identifiers, and unterminated CREATE TABLE patterns
- `tests/scripts/test_check_schema_migrations_false_negatives.py` covers three defect classes in the schema-index guard
- `tests/scripts/test_check_retrofit_migrations_false_negatives.py` covers three defect classes in the retrofit guard
