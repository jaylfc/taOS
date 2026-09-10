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

Dependency-light: sqlglot is a CI/dev-only dependency — it never lands on a Pi
(ARM64) because its Rust/C accelerators are opt-in extras. The pure-Python
version (30.18.0) has zero required runtime deps.
"""
from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# sqlglot is a CI/dev-only dependency — it never lands on a Pi (ARM64) because
# its Rust/C accelerators are opt-in extras. The pure-Python version (30.18.0)
# has zero required runtime deps.
try:
    import sqlglot
    from sqlglot import exp
    SQLGLOT_AVAILABLE = True
except ImportError:
    SQLGLOT_AVAILABLE = False

REPO_ROOT = Path(__file__).resolve().parent.parent

# A store is any Python file under tinyagentos/ that assigns SCHEMA (a string).
STORES_ROOT = REPO_ROOT / "tinyagentos"


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


def _parse_with_sqlglot(sql: str):
    """Parse SQL using sqlglot, returns a list of expressions."""
    if not SQLGLOT_AVAILABLE:
        return None
    try:
        parsed = sqlglot.parse(sql, dialect="sqlite")
        return parsed if isinstance(parsed, list) else [parsed]
    except Exception:
        return None


def _extract_table_columns(sql: str) -> dict[str, set[str]]:
    """Extract table names and their columns from CREATE TABLE statements."""
    tables = {}
    
    # Try sqlglot first for robust parsing (handles quoted identifiers)
    parsed = _parse_with_sqlglot(sql)
    if parsed is not None:
        for stmt in parsed:
            if isinstance(stmt, exp.Create) and isinstance(stmt.this, exp.Table):
                table_name = stmt.this.name
                columns = set()
                
                # Extract column names from ColumnDef expressions
                if hasattr(stmt, 'expressions'):
                    for col_def in stmt.expressions:
                        if isinstance(col_def, exp.ColumnDef):
                            columns.add(col_def.name)
                
                tables[table_name] = columns
        # If we successfully parsed with sqlglot, return now
        if tables:
            return tables
    
    # Fallback to regex-based extraction for compatibility
    _CREATE_TABLE_RE = re.compile(
        r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(["\w]+)\s*\((.*?)\)\s*;',
        re.IGNORECASE | re.DOTALL,
    )

    for tm in _CREATE_TABLE_RE.finditer(sql):
        table = tm.group(1).strip('"\'')
        cols_part = tm.group(2)
        
        # Simple column extraction for fallback
        columns = set()
        # Split by commas not inside parentheses
        depth = 0
        current = []
        for ch in cols_part:
            if ch == '(':
                depth += 1
            elif ch == ')':
                depth -= 1
            elif ch == ',' and depth == 0:
                segment = ''.join(current).strip()
                # Extract first word (column name)
                match = re.match(r'^(\w+)', segment, re.IGNORECASE)
                if match:
                    col_name = match.group(1).lower()
                    # Skip constraint keywords
                    if col_name not in {'primary', 'key', 'unique', 'check', 'foreign', 'references', 'constraint'}:
                        columns.add(col_name)
                current = []
            else:
                current.append(ch)
        
        if current:
            segment = ''.join(current).strip()
            match = re.match(r'^(\w+)', segment, re.IGNORECASE)
            if match:
                col_name = match.group(1).lower()
                if col_name not in {'primary', 'key', 'unique', 'check', 'foreign', 'references', 'constraint'}:
                    columns.add(col_name)
        
        tables[table] = columns
    
    return tables


def _extract_index_column_refs(sql: str) -> list[tuple[str, str]]:
    """Extract table and column references from CREATE INDEX statements."""
    index_refs = []
    
    # Try sqlglot first for robust parsing (handles expression indexes)
    parsed = _parse_with_sqlglot(sql)
    if parsed is not None:
        for stmt in parsed:
            if isinstance(stmt, exp.Create) and isinstance(stmt.this, exp.Index):
                # Find the table and columns in the Index expression
                table = None
                columns = []
                
                # Walk through the Index expression tree to find Table and Column nodes
                def walk_and_extract(node):
                    nonlocal table
                    
                    if isinstance(node, exp.Table):
                        table = node.name
                    
                    if isinstance(node, exp.Column):
                        columns.append(node.name)
                    
                    for child in node.iter_expressions():
                        walk_and_extract(child)
                
                walk_and_extract(stmt.this)
                
                if table and columns:
                    for col in columns:
                        index_refs.append((table, col))
        # If we successfully parsed with sqlglot, return now
        if index_refs:
            return index_refs
    
    # Fallback to regex-based extraction for compatibility
    _CREATE_INDEX_RE = re.compile(
        r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+ON\s+(["\w]+)\s*\((.+)\)',
        re.IGNORECASE,
    )

    for im in _CREATE_INDEX_RE.finditer(sql):
        table = im.group(1).strip('"\'')
        cols_part = im.group(2)
        indexed = [c.strip() for c in cols_part.split(",") if c.strip()]
        for col in indexed:
            col = col.strip()
            func_match = re.match(r"(?i)^\s*(\w+)\s*\(([^)]*)\)\s*$", col)
            if func_match:
                inner = func_match.group(2)
                for inner_col in inner.split(","):
                    inner_col = inner_col.strip()
                    if inner_col:
                        name = inner_col.split()[0].strip("`\"[]")
                        if name:
                            index_refs.append((table, name))
            else:
                col_name = col.split()[0] if col.split() else col
                col_name = col_name.strip("`\"[]")
                if col_name:
                    index_refs.append((table, col_name))
    
    return index_refs


def _extract_add_column_statements(source: str) -> set[tuple[str, str]]:
    """Extract ALTER TABLE ... ADD COLUMN statements from Python source."""
    added_columns: set[tuple[str, str]] = set()
    
    # Use regex to find ADD COLUMN statements (handles quoted identifiers)
    _ADD_COLUMN_RE = re.compile(
        r'ALTER\s+TABLE\s+(["\w]+)\s+ADD\s+(?:COLUMN\s+)?(["\w]+)',
        re.IGNORECASE,
    )
    
    for m in _ADD_COLUMN_RE.finditer(source):
        table = m.group(1).strip('"\'')
        column = m.group(2).strip('"\'')
        added_columns.add((table, column))
    
    return added_columns


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

    # Get all ALTER TABLE ADD COLUMN statements in the file
    added_columns = _extract_add_column_statements(source)

    violations: list[Violation] = []
    for schema in schema_strings:
        # Extract tables and columns from CREATE TABLE statements
        tables = _extract_table_columns(schema)

        # Extract index references
        index_refs = _extract_index_column_refs(schema)

        # Check each index reference
        for table, col in index_refs:
            # Check if this column was added by an ALTER TABLE statement
            if (table, col) in added_columns:
                # Check if the column is already defined in the table
                safe = False
                if table in tables:
                    safe = col.lower() in tables[table]

                if not safe:
                    # Find the original index statement for reporting
                    # Use regex to reconstruct the index statement
                    _CREATE_INDEX_RE = re.compile(
                        r'CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\w+\s+ON\s+(["\w]+)\s*\(([^)]*)\)',
                        re.IGNORECASE,
                    )
                    match = _CREATE_INDEX_RE.search(schema)
                    if match:
                        cols_part = match.group(1)
                        # Find the full index statement
                        for im in _CREATE_INDEX_RE.finditer(schema):
                            if im.group(1) == table:
                                index_stmt = im.group(0).strip()
                                break
                        else:
                            index_stmt = f"CREATE INDEX ON {table}({col})"
                    else:
                        index_stmt = f"CREATE INDEX ON {table}({col})"

                    violations.append(
                        Violation(
                            path=path,
                            table=table,
                            column=col,
                            index_stmt=index_stmt,
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
    root = Path(argv[0]) if argv else STORES_ROOT
    violations = find_all_violations(root)
    if not violations:
        print("schema-migration-guard: clean")
        return 0
    for v in violations:
        print(f"SCHEMA-MIGRATION VIOLATION: {v}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
