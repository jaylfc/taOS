### Added
- New `schema-column-guard` static check (`scripts/check_schema_column_migrations.py`) plus matching doc-gate step; flags any column added to a `CREATE TABLE` inside a store's `SCHEMA` with no matching `ALTER TABLE ... ADD COLUMN` in the same file, including the previously-uncovered case of zero migration at all (proven on PR #2416).

### Fixed
- `schema-column-guard` now resolves `SCHEMA` constants via AST so that docstrings and unrelated triple-quoted strings containing `CREATE TABLE` do not produce false positives.
- `schema-column-guard` now only matches `ALTER TABLE ... ADD COLUMN` inside the string literals of a `_post_init` **method**, read out of the AST; comments and docstrings can no longer silence a column, a `#` inside an SQL literal can no longer swallow one, and a module-level helper named `_post_init` no longer speaks for every store in the file.
- `schema-column-guard` now exits non-zero with a loud warning when the baseline ref is missing, instead of silently treating every column as a violation.
- `schema-column-guard` compares against the PR's own base branch (`--base`, `BASE_REF`, default `origin/dev`), so a `master`-targeted PR is no longer blocked by a ref its checkout never fetched.
- `schema-column-guard` parses the baseline snapshot with AST too, so a `CREATE TABLE` in a docstring on the base branch can no longer invent a baseline column and mask a real violation.
- `schema-column-guard` resolves each `SCHEMA` in its own lexical scope, so two classes that both alias a same-named constant no longer collapse onto the first value; f-strings and `+`-concatenated literals resolve, and a `SCHEMA` it cannot resolve is reported on stderr as unchecked rather than skipped in silence.
- `schema-column-guard` treats a file it cannot read or parse as a hard failure (exit 2) instead of reporting the run clean, and prints accumulated violations before that exit so one CI run shows the full picture.
- `schema-column-guard` decides whether a file exists on the baseline from `git cat-file -e`'s exit status rather than by matching git's stderr wording, which is version-dependent and localized.
- `schema-column-guard` strips SQL `--` and `/* */` comments before splitting a `CREATE TABLE` body. An inline comment runs to the end of its line including the comma that ends the column, so previously every column declared after the first commented one was invisible to the guard - a store documenting its columns inline (the house style) was almost entirely unchecked.
- `schema-column-guard` only diffs tables that already exist on the baseline. A brand-new table is built in full by its own `CREATE TABLE IF NOT EXISTS` on every install, so no ALTER applies; diffing it emitted one violation per column on every new store.
