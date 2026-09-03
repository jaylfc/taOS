#!/usr/bin/env python3
"""Static guard for SCHEMA-only column adds with no migration (tsk-hrzgip).

``BaseStore.init()`` runs a store's ``SCHEMA`` string (CREATE TABLE + CREATE
INDEX) at boot. ``CREATE TABLE IF NOT EXISTS`` is a no-op on existing
databases, so a new column added straight into the ``CREATE TABLE`` body is
silently absent on upgrade. The first INSERT or SELECT that touches it then
crashes with ``table <t> has no column named <c>`` on every existing install.
This is the brick that the two existing migration guards cannot see, because
neither has a migration entry to inspect (taOS PR #2416 proved both clean on
exactly this case).

The mandated pattern is a guarded ``_post_init`` coroutine: ``PRAGMA
table_info`` check + ``ALTER TABLE <t> ADD COLUMN <c>`` only when absent.

This script statically inspects every Python file under ``tinyagentos/`` and
flags, for every CREATE TABLE in a SCHEMA string: a column that

  (a) is declared in that CREATE TABLE in the CURRENT file, AND
  (b) has NO matching ``ALTER TABLE <t> ADD [COLUMN] <c>`` inside a
      ``_post_init`` body in the SAME file (no _post_init migration runs it),
      AND
  (c) was NOT in the CREATE TABLE column list on ``origin/dev`` (it is newly
      added by this change -- so existing columns that have always lived in
      SCHEMA do not trip the guard).

The third condition is what keeps it from flagging every column in the
repo: comparison is done against ``git show origin/dev:<path>`` for the same
file, only NEW columns relative to that snapshot count.

CI does NOT catch it because tests build fresh databases (which always have
the column); this guard exists to fill that gap.

Usage:
    python scripts/check_schema_column_migrations.py
Invoked by the ``schema-column-guard`` step in
``.github/workflows/doc-gate.yml``.

Prints ``schema-column-guard: clean`` and exits 0 when no violations, or
prints each violation and exits 1.

Dependency-light: stdlib only (re + pathlib + subprocess).
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STORES_ROOT = REPO_ROOT / "tinyagentos"

# Tables that are not "user data" stores and therefore not subject to this
# guard. The migration runner's bookkeeping table is created on first boot
# before any store init runs, so columns added to it on origin/dev are
# genuinely new for the very DBs that need them and would generate noise.
_EXCLUDED_TABLES = frozenset({"schema_migrations"})

# CREATE TABLE [IF NOT EXISTS] <table> ( ... )
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\)\s*;",
    re.IGNORECASE | re.DOTALL,
)

# ALTER TABLE <table> ADD [COLUMN] <col> ...
_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+(?:COLUMN\s+)?(\w+)",
    re.IGNORECASE,
)


class RefError(Exception):
    """Raised when the origin/dev baseline cannot be read."""


@dataclass
class Violation:
    path: Path
    table: str
    column: str
    detail: str

    def __str__(self) -> str:
        fix = (
            "add a guarded _post_init coroutine that ALTERs this column "
            "into place after a PRAGMA table_info check"
        )
        return (
            f"{self.path}: table '{self.table}', column '{self.column}' "
            f"added to SCHEMA with no migration\n"
            f"    detail: {self.detail}\n"
            f"    fix: {fix}"
        )


def _split_columns(body: str) -> set[str]:
    """Extract declared column names from a CREATE TABLE body.

    Splits on commas that are not inside parentheses (so function/default
    expressions and inline CHECK(...) are handled), then keeps the first
    whitespace-delimited token of each segment as the column name (unless
    that segment is an inline table-level constraint, which does not add
    a column).

    NOTE: This scanner does not handle a DEFAULT expression that contains a
    top-level comma, or a quoted column name that collides with one of the
    non-column keywords. Neither is fatal for the current store schemas.
    """
    _INLINE_CONSTRAINT_RE = re.compile(
        r"^\s*(?:CONSTRAINT\s+\w+\s+)?(?:PRIMARY\s+KEY|UNIQUE|CHECK|FOREIGN\s+KEY|REFERENCES)\b",
        re.IGNORECASE,
    )
    _COLUMN_NAME_RE = re.compile(r"^\s*(\w+)\s+", re.IGNORECASE)
    _NON_COLUMN_KEYWORDS = {
        "create", "table", "primary", "key", "unique", "check", "foreign",
        "references", "constraint", "default", "not", "null", "integer",
        "text", "real", "blob", "numeric", "autoincrement", "if", "exists",
        "select", "on", "and", "or", "as", "collate", "generated", "always",
    }

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
            continue
        m = _COLUMN_NAME_RE.match(seg)
        if not m:
            continue
        name = m.group(1)
        if name.lower() in _NON_COLUMN_KEYWORDS:
            continue
        columns.add(name)
    return columns


def _check_origin_dev_ref() -> bool:
    """Return True if origin/dev is a valid ref the local repo can read."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "origin/dev"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except OSError:
        return False


def _origin_dev_columns(path: Path) -> dict[str, set[str]] | None:
    """Return {table: columns} for CREATE TABLE bodies in the file on
    ``origin/dev``. Returns ``{}`` when the file is new on origin/dev.
    Returns ``None`` when the ref or file cannot be read (caller should
    treat as a hard error)."""
    rel = path.relative_to(REPO_ROOT)
    try:
        result = subprocess.run(
            ["git", "show", f"origin/dev:{rel.as_posix()}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        print(
            f"schema-column-guard: ERROR reading origin/dev baseline for "
            f"{rel}: {exc}",
            file=sys.stderr,
        )
        return None
    if result.returncode != 0:
        stderr = result.stderr.lower()
        if "pathspec" in stderr and "did not match" in stderr:
            return {}
        print(
            f"schema-column-guard: ERROR reading origin/dev baseline for "
            f"{rel}: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return None
    out: dict[str, set[str]] = {}
    for tm in _CREATE_TABLE_RE.finditer(result.stdout):
        out[tm.group(1)] = _split_columns(tm.group(2))
    return out


def _strip_comments_and_docstrings(source: str) -> str:
    """Remove # comments and triple-quoted strings from source."""
    source = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
    source = re.sub(r"'''.*?'''", "", source, flags=re.DOTALL)
    source = "\n".join(line.split("#")[0] for line in source.splitlines())
    return source


def _get_post_init_sources(source: str, tree: ast.AST) -> list[str]:
    """Extract source text of _post_init function/method bodies."""
    sources: list[str] = []
    lines = source.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "_post_init":
            start = node.lineno - 1
            end = node.end_lineno
            sources.append("\n".join(lines[start:end]))
    return sources


def _extract_schemas(tree: ast.AST) -> list[str]:
    """Find SCHEMA string constants using AST."""
    out: list[str] = []
    seen: set[str] = set()
    string_constants: dict[str, str] = {}

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                        string_constants[target.id] = node.value.value
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.value is not None:
                if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                    string_constants[node.target.id] = node.value.value

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

    def _resolve(node: ast.AST) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name) and node.id in string_constants:
            return string_constants[node.id]
        return None

    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SCHEMA":
                    value = _resolve(node.value)
                    if value is not None and value not in seen:
                        seen.add(value)
                        out.append(value)
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name) and node.target.id == "SCHEMA":
                value = _resolve(node.value)
                if value is not None and value not in seen:
                    seen.add(value)
                    out.append(value)

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.Assign):
                    for target in item.targets:
                        if isinstance(target, ast.Name) and target.id == "SCHEMA":
                            value = _resolve(item.value)
                            if value is not None and value not in seen:
                                seen.add(value)
                                out.append(value)
                elif isinstance(item, ast.AnnAssign):
                    if isinstance(item.target, ast.Name) and item.target.id == "SCHEMA":
                        value = _resolve(item.value)
                        if value is not None and value not in seen:
                            seen.add(value)
                            out.append(value)

    return out


def find_violations(path: Path) -> list[Violation]:
    """Run the static check against a single Python file. Returns violations."""
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return []

    schemas = _extract_schemas(tree)
    if not schemas:
        return []

    origin_cols = _origin_dev_columns(path)
    if origin_cols is None:
        raise RefError(
            f"failed to read origin/dev baseline for {path}"
        )

    added_columns: set[tuple[str, str]] = set()
    for sql_source in _get_post_init_sources(source, tree):
        clean = _strip_comments_and_docstrings(sql_source)
        for m in _ADD_COLUMN_RE.finditer(clean):
            added_columns.add((m.group(1), m.group(2)))

    violations: list[Violation] = []
    for schema in schemas:
        for tm in _CREATE_TABLE_RE.finditer(schema):
            table = tm.group(1)
            if table in _EXCLUDED_TABLES:
                continue
            current_cols = _split_columns(tm.group(2))
            baseline_cols = origin_cols.get(table, set())
            new_cols = current_cols - baseline_cols
            for col in sorted(new_cols):
                if (table, col) not in added_columns:
                    violations.append(Violation(
                        path=path,
                        table=table,
                        column=col,
                        detail=f"new column '{col}' in CREATE TABLE {table} with no ALTER TABLE {table} ADD COLUMN {col} in this file",
                    ))
    return violations


def find_all_violations(root: Path = STORES_ROOT) -> tuple[list[Violation], bool]:
    """Walk every Python file under the stores root and collect violations.
    Returns (violations, had_error) where had_error is True if a baseline
    read failed."""
    violations: list[Violation] = []
    had_error = False
    if not root.is_dir():
        return violations, had_error
    for py_file in sorted(root.rglob("*.py")):
        try:
            violations.extend(find_violations(py_file))
        except RefError as exc:
            print(str(exc), file=sys.stderr)
            had_error = True
    return violations, had_error


def main(argv: list[str] | None = None) -> int:
    if not _check_origin_dev_ref():
        print(
            "schema-column-guard: ERROR: origin/dev ref is missing; "
            "this guard requires it to compare new columns. "
            "Ensure origin/dev is fetched and accessible.",
            file=sys.stderr,
        )
        return 2
    args = argv if argv is not None else sys.argv[1:]
    root = Path(args[0]) if args else STORES_ROOT
    violations, had_error = find_all_violations(root)
    if had_error:
        return 2
    if not violations:
        print("schema-column-guard: clean")
        return 0
    for v in violations:
        print(f"SCHEMA-COLUMN VIOLATION: {v}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
