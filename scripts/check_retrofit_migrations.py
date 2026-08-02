#!/usr/bin/env python3
"""Static guard against retrofit migrations placed in MIGRATIONS (#tsk-gqsuah).

``BaseStore.init()`` runs a store's ``SCHEMA`` string (CREATE TABLE +
CREATE INDEX) BEFORE the migration runner.  The runner uses
baseline-at-latest semantics: pre-existing databases are stamped at the
latest version WITHOUT executing any SQL, so any migration that ALTERs
or adds an index to a table that ALSO appears in SCHEMA is silently
skipped on exactly the databases that need it.  The tracking table then
records the migration as applied -- a false success.

The correct pattern (mandated in the BaseStore docstring and
``db_migrations.py`` footgun #2) is a guarded ``_post_init`` coroutine:
``PRAGMA table_info`` check + ``ALTER TABLE`` only when the column is
absent.  Such a migration runs every init, stays permanently in the
codebase, and self-checks whether it already ran.

This script statically inspects every Python file under ``tinyagentos/``
that defines a store and flags the dangerous pattern: a MIGRATIONS entry
that ALTERs or adds a CREATE INDEX to a table that ALSO appears in that
store's SCHEMA -- i.e. a retrofit to a pre-existing table that
baseline-at-latest will skip.

It does NOT flag:
  - CREATE TABLE IF NOT EXISTS in MIGRATIONS (genuinely new tables are
    safe: idempotent on existing DBs).
  - Migrations that reference the SCHEMA constant itself (the bootstrap
    pattern ``MIGRATIONS = [(1, SCHEMA)]``), since that is the initial
    schema creation, not a retrofit.

Usage:
    python scripts/check_retrofit_migrations.py
Invoked by the ``retrofit-migration-guard`` step in
``.github/workflows/doc-gate.yml``.

Prints ``retrofit-migration-guard: clean`` and exits 0 when no violations,
or prints each violation and exits 1.

Dependency-light: stdlib only (ast + re + pathlib).
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STORES_ROOT = REPO_ROOT / "tinyagentos"

# ALTER TABLE <table> ADD [COLUMN] <col> ...
_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+(?:COLUMN\s+)?(\w+)",
    re.IGNORECASE,
)

# CREATE [UNIQUE] INDEX [IF NOT EXISTS] <name> ON <table> (col, col, ...)
_CREATE_INDEX_RE = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+ON\s+(\w+)",
    re.IGNORECASE,
)

# CREATE TABLE [IF NOT EXISTS] <table> ( ... )
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\(",
    re.IGNORECASE,
)


@dataclass
class Violation:
    path: Path
    store: str
    version: int
    table: str
    kind: str  # "ALTER TABLE ADD COLUMN" or "CREATE INDEX"
    detail: str

    def __str__(self) -> str:
        if self.kind == "CREATE INDEX":
            fix = (
                "move this index creation into a guarded _post_init coroutine "
                "(PRAGMA index_list check + CREATE INDEX IF NOT EXISTS only when absent)"
            )
        else:
            fix = (
                "move this migration into a guarded _post_init coroutine "
                "(PRAGMA table_info check + ALTER TABLE only when absent)"
            )
        return (
            f"{self.path}: store '{self.store}', migration v{self.version}, "
            f"table '{self.table}' -- {self.kind} on a SCHEMA table\n"
            f"    detail: {self.detail}\n"
            f"    fix: {fix}"
        )


def _resolve_string(node: ast.AST, string_constants: dict[str, str]) -> str | None:
    """Resolve an AST node to a string value, following constant references."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name) and node.id in string_constants:
        return string_constants[node.id]
    return None


def _resolve_list_node(node: ast.AST, list_constants: dict[str, ast.List]) -> ast.List | None:
    """Resolve an AST node to a list node, following constant references."""
    if isinstance(node, ast.List):
        return node
    if isinstance(node, ast.Name) and node.id in list_constants:
        return list_constants[node.id]
    return None


def _normalize(sql: str) -> str:
    """Normalize SQL for comparison: collapse whitespace, lowercase."""
    return re.sub(r"\s+", " ", sql).strip().lower()


def _extract_schema_tables(schema_sql: str) -> set[str]:
    """Return the set of table names declared in CREATE TABLE statements."""
    return {m.group(1) for m in _CREATE_TABLE_RE.finditer(schema_sql)}


def _check_migration_sql(
    sql: str,
    schema_tables: set[str],
    schema_sql_normalized: str,
    path: Path,
    store: str,
    version: int,
) -> list[Violation]:
    """Check a single migration SQL string for retrofit patterns."""
    violations: list[Violation] = []

    # Bootstrap: if the migration SQL is the SCHEMA itself (initial creation),
    # skip it -- CREATE TABLE IF NOT EXISTS and CREATE INDEX IF NOT EXISTS are
    # idempotent and this is not a retrofit.
    if _normalize(sql) == schema_sql_normalized:
        return violations

    # ALTER TABLE <schema_table> ADD COLUMN <col> -> retrofit
    for m in _ADD_COLUMN_RE.finditer(sql):
        table = m.group(1)
        if table in schema_tables:
            violations.append(Violation(
                path=path,
                store=store,
                version=version,
                table=table,
                kind="ALTER TABLE ADD COLUMN",
                detail=m.group(0).strip(),
            ))

    # CREATE INDEX ... ON <schema_table> -> retrofit (baseline skips it)
    for m in _CREATE_INDEX_RE.finditer(sql):
        table = m.group(1)
        if table in schema_tables:
            violations.append(Violation(
                path=path,
                store=store,
                version=version,
                table=table,
                kind="CREATE INDEX",
                detail=m.group(0).strip(),
            ))

    return violations


def find_violations(path: Path) -> list[Violation]:
    """Run the static check against a single Python file. Returns violations."""
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return []

    # Collect all module-level string/list constant assignments (name -> value).
    string_constants: dict[str, str] = {}
    list_constants: dict[str, ast.List] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        string_constants[target.id] = node.value.value
                    elif isinstance(node.value, ast.List):
                        list_constants[target.id] = node.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    string_constants[node.target.id] = node.value.value
                elif isinstance(node.value, ast.List):
                    list_constants[node.target.id] = node.value

    # Also collect string constants defined inside class bodies (for
    # SCHEMA = "..." inside a class), including annotated assignments
    # (e.g. SCHEMA: str = "...").
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    if target.id not in string_constants:
                        string_constants[target.id] = node.value.value
        elif isinstance(node, ast.AnnAssign):
            if (
                isinstance(node.target, ast.Name)
                and node.value is not None
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                if node.target.id not in string_constants:
                    string_constants[node.target.id] = node.value.value

    violations: list[Violation] = []

    # --- Module-level stores (e.g. scheduling/worker_heartbeat.py) ---
    module_schema: str | None = None
    module_migrations: ast.List | None = None
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if target.id == "SCHEMA":
                        module_schema = _resolve_string(node.value, string_constants)
                    elif target.id == "MIGRATIONS":
                        module_migrations = _resolve_list_node(node.value, list_constants)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                if node.target.id == "SCHEMA":
                    module_schema = _resolve_string(node.value, string_constants)
                elif node.target.id == "MIGRATIONS":
                    module_migrations = _resolve_list_node(node.value, list_constants)

    if module_schema and module_migrations:
        schema_tables = _extract_schema_tables(module_schema)
        schema_norm = _normalize(module_schema)
        store_name = path.stem
        violations.extend(
            _check_migrations_list(module_migrations, string_constants, schema_tables, schema_norm, path, store_name)
        )

    # --- Class-level stores (e.g. contacts_store.py, invite_store.py) ---
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        class_schema: str | None = None
        class_migrations: ast.List | None = None
        for item in node.body:
            if isinstance(item, ast.Assign):
                for target in item.targets:
                    if isinstance(target, ast.Name):
                        if target.id == "SCHEMA":
                            class_schema = _resolve_string(item.value, string_constants)
                        elif target.id == "MIGRATIONS":
                            class_migrations = _resolve_list_node(item.value, list_constants)
            elif isinstance(item, ast.AnnAssign):
                if isinstance(item.target, ast.Name):
                    if item.target.id == "SCHEMA":
                        class_schema = _resolve_string(item.value, string_constants)
                    elif item.target.id == "MIGRATIONS":
                        class_migrations = _resolve_list_node(item.value, list_constants)

        if class_schema and class_migrations:
            schema_tables = _extract_schema_tables(class_schema)
            schema_norm = _normalize(class_schema)
            violations.extend(
                _check_migrations_list(class_migrations, string_constants, schema_tables, schema_norm, path, node.name)
            )

    return violations


def _check_migrations_list(
    migrations_node: ast.List,
    string_constants: dict[str, str],
    schema_tables: set[str],
    schema_norm: str,
    path: Path,
    store_name: str,
) -> list[Violation]:
    """Check each (version, sql) entry in a MIGRATIONS list AST node."""
    violations: list[Violation] = []
    for elt in migrations_node.elts:
        if not isinstance(elt, ast.Tuple) or len(elt.elts) < 2:
            continue
        version_node = elt.elts[0]
        sql_node = elt.elts[1]

        # Resolve version (must be a constant int).
        if not isinstance(version_node, ast.Constant) or not isinstance(version_node.value, int):
            continue
        version = version_node.value

        # Resolve SQL: string literal or constant reference.
        sql = _resolve_string(sql_node, string_constants)
        if sql is None:
            # Callable (function) or unresolvable -- cannot statically analyze.
            continue

        violations.extend(
            _check_migration_sql(sql, schema_tables, schema_norm, path, store_name, version)
        )

    return violations


def find_all_violations(root: Path = STORES_ROOT) -> list[Violation]:
    """Walk every Python file under the stores root and collect violations."""
    violations: list[Violation] = []
    if not root.is_dir():
        return violations
    for py_file in sorted(root.rglob("*.py")):
        violations.extend(find_violations(py_file))
    return violations


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    root = Path(args[0]) if args else STORES_ROOT
    violations = find_all_violations(root)
    if not violations:
        print("retrofit-migration-guard: clean")
        return 0
    for v in violations:
        print(f"RETROFIT MIGRATION VIOLATION: {v}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
