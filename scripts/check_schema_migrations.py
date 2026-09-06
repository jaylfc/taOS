#!/usr/bin/env python3
"""Static guard against the SCHEMA-index-before-migration boot brick (taOS #1865).

``BaseStore.init()`` runs a store's ``SCHEMA`` string (via executescript)
BEFORE ``_post_init()`` where ALTER-COLUMN migrations run. So any
``CREATE INDEX ... (X)`` inside the ``SCHEMA`` string that references a column
X which is ADDED by a ``_post_init`` migration (``ALTER TABLE ... ADD COLUMN X``)
crashes boot with ``no such column: X`` on an EXISTING pre-change DB. This has
bricked the production Pi multiple times; CI never catches it because tests
build fresh DBs (which have the column).

This script statically inspects every Python file under ``tinyagentos/`` that
defines a store and flags the dangerous pattern: a column that is
  (a) referenced by a CREATE INDEX / UNIQUE INDEX statement INSIDE the SCHEMA
      string,
  (b) added via ``ALTER TABLE ... ADD COLUMN`` in the same file (the migration
      that runs AFTER the SCHEMA, in ``_post_init``), and
  (c) NOT present in that table's CREATE TABLE column list (so it cannot be a
      defensive double-declare).

Usage:
    python scripts/check_schema_migrations.py
Prints ``schema-migration-guard: clean`` and exits 0 when no violations, or
prints each violation and exits 1.

Dependency-light: stdlib only (ast + re + pathlib).
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# A store is any Python file under tinyagentos/ that assigns SCHEMA (a string).
STORES_ROOT = REPO_ROOT / "tinyagentos"

# CREATE [UNIQUE] INDEX [IF NOT EXISTS] <name> ON <table> (col, col, ...)
_CREATE_INDEX_RE = re.compile(
    r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+ON\s+(\w+)\s*\(([^)]*)\)",
    re.IGNORECASE,
)

# CREATE TABLE [IF NOT EXISTS] <table> ( ... )
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)

# A single column definition line/segment inside a CREATE TABLE body.
# We match leading column names that are NOT constraint keywords.
_COLUMN_NAME_RE = re.compile(r"^\s*(\w+)\s+", re.IGNORECASE)

# Inline UNIQUE/PKEY/CHECK/FK/... constraints inside a CREATE TABLE body that
# reference columns but do NOT add a new column (so the column is "safe").
_INLINE_CONSTRAINT_RE = re.compile(
    r"^\s*(?:CONSTRAINT\s+\w+\s+)?(?:PRIMARY\s+KEY|UNIQUE|CHECK|FOREIGN\s+KEY|REFERENCES)\b",
    re.IGNORECASE,
)

# ALTER TABLE <table> ADD COLUMN <col> ...
_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+(?:COLUMN\s+)?(\w+)",
    re.IGNORECASE,
)

# Keyword names that may appear where a column name would in a column def;
# these are not candidate column names.
_NON_COLUMN_KEYWORDS = {
    "create", "table", "primary", "key", "unique", "check", "foreign",
    "references", "constraint", "default", "not", "null", "integer", "text",
    "real", "blob", "numeric", "autoincrement", "if", "exists", "select",
    "on", "and", "or", "as", "collate", "generated", "always",
}


@dataclass
class Violation:
    path: Path
    table: str
    column: str
    index_stmt: str

    def __str__(self) -> str:
        fix = (
            "move this index into _post_init after the ALTER, "
            "using CREATE INDEX IF NOT EXISTS"
        )
        return (
            f"{self.path}: table '{self.table}', column '{self.column}' "
            f"indexed before migration\n"
            f"    offending SCHEMA index: {self.index_stmt}\n"
            f"    fix: {fix}"
        )


def _split_columns(body: str) -> set[str]:
    """Extract declared column names from a CREATE TABLE body.

    Splits on commas that are not inside parentheses (so function/default
    expressions and inline CHECK(...) are handled), then keeps the first
    whitespace-delimited token of each segment as the column name (unless that
    segment is an inline table-level constraint, which does not add a column).
    """
    columns: set[str] = set()
    depth = 0
    segments: list[str] = []
    current: list[str] = []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            segments.append("".join(current))
            current = []
        else:
            current.append(ch)
    segments.append("".join(current))

    for seg in segments:
        if _INLINE_CONSTRAINT_RE.match(seg):
            # Might be `UNIQUE (a, b)` -- those columns are safe (declared in
            # CREATE TABLE), but it does not *add* a column. Skip as a column.
            continue
        m = _COLUMN_NAME_RE.match(seg)
        if not m:
            continue
        name = m.group(1)
        if name.lower() in _NON_COLUMN_KEYWORDS:
            continue
        columns.add(name)
    return columns


def find_violations(path: Path) -> list[Violation]:
    """Run the static check against a single Python file. Returns violations."""
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return []

    schema_strings: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        # SCHEMA = "..."  or  SCHEMA = SOME_CONST
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "SCHEMA":
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    schema_strings.append(node.value.value)
                elif isinstance(node.value, ast.Name):
                    # SCHEMA = NOTIF_SCHEMA -> resolve the referenced constant.
                    ref = node.value.id
                    for sub in ast.walk(tree):
                        if (
                            isinstance(sub, ast.Assign)
                            and len(sub.targets) == 1
                            and isinstance(sub.targets[0], ast.Name)
                            and sub.targets[0].id == ref
                            and isinstance(sub.value, ast.Constant)
                            and isinstance(sub.value.value, str)
                        ):
                            schema_strings.append(sub.value.value)

    if not schema_strings:
        return []

    # ADD COLUMN migrations anywhere in the file (they run after SCHEMA).
    added_columns: set[tuple[str, str]] = set()
    for m in _ADD_COLUMN_RE.finditer(source):
        added_columns.add((m.group(1), m.group(2)))

    violations: list[Violation] = []
    for schema in schema_strings:
        # Tables declared in CREATE TABLE are safe (column already exists).
        safe_columns: set[tuple[str, str]] = set()
        for tm in _CREATE_TABLE_RE.finditer(schema):
            table = tm.group(1)
            for col in _split_columns(tm.group(2)):
                safe_columns.add((table, col))

        # Every index INSIDE the SCHEMA string.
        for im in _CREATE_INDEX_RE.finditer(schema):
            table = im.group(1)
            cols_part = im.group(2)
            indexed = [c.strip() for c in cols_part.split(",") if c.strip()]
            for col in indexed:
                # Strip a trailing direction/collation qualifier (e.g. "DESC").
                col_name = col.split()[0] if col.split() else col
                col_name = col_name.strip("`\"[]")
                if not col_name:
                    continue
                if (table, col_name) in added_columns and (table, col_name) not in safe_columns:
                    violations.append(
                        Violation(
                            path=path,
                            table=table,
                            column=col_name,
                            index_stmt=im.group(0).strip(),
                        )
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
    violations = find_all_violations()
    if not violations:
        print("schema-migration-guard: clean")
        return 0
    for v in violations:
        print(f"SCHEMA-MIGRATION VIOLATION: {v}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
