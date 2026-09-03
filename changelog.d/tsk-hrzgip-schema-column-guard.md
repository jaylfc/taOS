### Added
- New `schema-column-guard` static check (`scripts/check_schema_column_migrations.py`) plus matching doc-gate step; flags any column added to a `CREATE TABLE` inside a store's `SCHEMA` with no matching `ALTER TABLE ... ADD COLUMN` in the same file, including the previously-uncovered case of zero migration at all (proven on PR #2416).

### Fixed
- `schema-column-guard` now resolves `SCHEMA` constants via AST so that docstrings and unrelated triple-quoted strings containing `CREATE TABLE` do not produce false positives.
- `schema-column-guard` now only matches `ALTER TABLE ... ADD COLUMN` inside `_post_init` bodies (with comments and docstrings stripped), preventing false negatives from mentions in comments or module docstrings.
- `schema-column-guard` now exits non-zero with a loud warning when the `origin/dev` baseline ref is missing, instead of silently treating every column as a violation.
