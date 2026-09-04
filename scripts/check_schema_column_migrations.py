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
      ``_post_init`` METHOD body in the SAME file (no _post_init migration
      runs it), AND
  (c) was NOT in the CREATE TABLE column list on the baseline ref (it is
      newly added by this change -- so existing columns that have always
      lived in SCHEMA do not trip the guard).

The third condition is what keeps it from flagging every column in the
repo: comparison is done against ``git show <baseline-ref>:<path>`` for the
same file, only NEW columns relative to that snapshot count. The baseline ref
defaults to ``origin/dev`` and follows the PR's base branch when ``--base`` or
``BASE_REF`` is supplied, so a ``master``-targeted hotfix compares against
``origin/master`` instead of demanding a ref its checkout never fetched.

Both the current file and the baseline snapshot are parsed with ``ast``: only
real ``SCHEMA`` assignments count, so a CREATE TABLE inside a docstring or an
unrelated string on either side can neither invent a violation nor mask one.

CI does NOT catch it because tests build fresh databases (which always have
the column); this guard exists to fill that gap.

Usage:
    python scripts/check_schema_column_migrations.py [ROOT] [--base origin/dev]
Invoked by the ``schema-column-guard`` step in
``.github/workflows/doc-gate.yml``.

Prints ``schema-column-guard: clean`` and exits 0 when no violations, prints
each violation and exits 1 when there are, and exits 2 when a file or a
baseline could not be read at all (a file the guard cannot parse is a hard
failure, never a silent skip).

Dependency-light: stdlib only (ast + re + pathlib + subprocess).
"""
from __future__ import annotations

import argparse
import ast
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STORES_ROOT = REPO_ROOT / "tinyagentos"

# Baseline the current tree is compared against when nothing else says
# otherwise. CI passes the PR's own base branch instead (see --base).
DEFAULT_BASELINE_REF = "origin/dev"

# Tables that are not "user data" stores and therefore not subject to this
# guard. The migration runner's bookkeeping table is created on first boot
# before any store init runs, so columns added to it on the baseline are
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


class GuardError(Exception):
    """A file the guard was asked to check could not be checked."""


class RefError(GuardError):
    """Raised when the baseline snapshot of a file cannot be read."""


class SourceError(GuardError):
    """Raised when a file in the working tree cannot be read or parsed."""


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


def _baseline_ref(explicit: str | None = None) -> str:
    """Resolve the ref the working tree is compared against.

    Precedence: an explicit ``--base`` value, then ``SCHEMA_COLUMN_BASE_REF``,
    then ``BASE_REF`` (what the CI workflow exports from ``github.base_ref``),
    then ``origin/dev``. A bare branch name is qualified to ``origin/<name>``
    so the workflow can hand the check its own base branch verbatim.
    """
    raw = (
        explicit
        or os.environ.get("SCHEMA_COLUMN_BASE_REF")
        or os.environ.get("BASE_REF")
        or ""
    ).strip()
    if not raw:
        return DEFAULT_BASELINE_REF
    if raw.startswith(("origin/", "refs/")):
        return raw
    return f"origin/{raw}"


def _check_base_ref(ref: str) -> bool:
    """Return True if ``ref`` is a valid ref the local repo can read."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        return result.returncode == 0
    except OSError:
        return False


def _rel_spec(path: Path, ref: str) -> str:
    """Build the ``<ref>:<path>`` revision spec for a working-tree file."""
    try:
        rel = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        rel = path
    return f"{ref}:{rel.as_posix()}"


def _baseline_columns(path: Path, ref: str) -> dict[str, set[str]] | None:
    """Return {table: columns} for CREATE TABLE bodies declared in the file's
    SCHEMA strings on ``ref``.

    Returns ``{}`` when the file does not exist on ``ref`` (it is new).
    Returns ``None`` when the ref or the file cannot be read, or the baseline
    snapshot cannot be parsed (caller treats that as a hard error).

    Existence is decided by ``git cat-file -e``'s exit status, never by
    matching git's stderr wording, which is version-dependent and localized.
    """
    spec = _rel_spec(path, ref)
    try:
        exists = subprocess.run(
            ["git", "cat-file", "-e", spec],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        if exists.returncode != 0:
            # Either the path is absent on ref (a new file, fine) or the ref
            # itself is unusable (a hard error). Tell them apart by asking
            # about the ref alone.
            if _check_base_ref(ref):
                return {}
            print(
                f"schema-column-guard: ERROR reading {ref} baseline for "
                f"{spec}: ref is not readable",
                file=sys.stderr,
            )
            return None
        result = subprocess.run(
            ["git", "show", spec],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        print(
            f"schema-column-guard: ERROR reading {ref} baseline for "
            f"{spec}: {exc}",
            file=sys.stderr,
        )
        return None
    if result.returncode != 0:
        print(
            f"schema-column-guard: ERROR reading {ref} baseline for "
            f"{spec}: {result.stderr.strip()}",
            file=sys.stderr,
        )
        return None
    try:
        baseline_tree = ast.parse(result.stdout)
    except SyntaxError as exc:
        print(
            f"schema-column-guard: ERROR: baseline {spec} does not parse: {exc}",
            file=sys.stderr,
        )
        return None

    out: dict[str, set[str]] = {}
    for schema in _extract_schemas(baseline_tree, label=spec):
        for tm in _CREATE_TABLE_RE.finditer(schema):
            out.setdefault(tm.group(1), set()).update(_split_columns(tm.group(2)))
    return out


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """Node ids of every docstring Constant in ``tree``.

    A docstring is prose, not executable SQL, so an ``ALTER TABLE`` written
    inside one must never silence a violation.
    """
    ids: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            ids.add(id(body[0].value))
    return ids


def _post_init_added_columns(tree: ast.AST) -> set[tuple[str, str]]:
    """Collect ``(table, column)`` pairs ALTERed in ``_post_init`` METHODS.

    Only a ``_post_init`` defined directly in a class body counts: a
    module-level helper of the same name is not the migration hook
    ``BaseStore.init()`` calls, so its ALTERs must not silence any store.

    SQL is read out of the AST's string constants rather than out of stripped
    source text, so ``#`` inside a SQL literal cannot chop the statement and a
    triple-quoted SQL literal is not mistaken for a docstring.
    """
    doc_ids = _docstring_constant_ids(tree)
    added: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if item.name != "_post_init":
                continue
            for sub in ast.walk(item):
                if (
                    isinstance(sub, ast.Constant)
                    and isinstance(sub.value, str)
                    and id(sub) not in doc_ids
                ):
                    for m in _ADD_COLUMN_RE.finditer(sub.value):
                        added.add((m.group(1), m.group(2)))
    return added


def _resolve(node: ast.AST, scope: dict[str, str]) -> str | None:
    """Resolve an assignment value to a string, or None if it is not static.

    Handles plain constants, names bound in ``scope``, f-strings whose parts
    all resolve, and ``+`` concatenation of resolvable parts.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return scope.get(node.id)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            elif isinstance(value, ast.FormattedValue):
                inner = _resolve(value.value, scope)
                if inner is None:
                    return None
                parts.append(inner)
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _resolve(node.left, scope)
        right = _resolve(node.right, scope)
        if left is None or right is None:
            return None
        return left + right
    return None


def _scope_constants(
    body: list[ast.stmt], inherited: dict[str, str]
) -> dict[str, str]:
    """Extend ``inherited`` with the string constants bound in ``body``.

    ``body`` is a module or class body: a name bound here shadows the same
    name from an enclosing scope, which is what keeps two classes that both
    define ``SQL`` from stealing each other's schema.
    """
    scope = dict(inherited)
    for stmt in body:
        if isinstance(stmt, ast.Assign):
            value = _resolve(stmt.value, scope)
            if value is None:
                continue
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    scope[target.id] = value
        elif isinstance(stmt, ast.AnnAssign):
            if isinstance(stmt.target, ast.Name) and stmt.value is not None:
                value = _resolve(stmt.value, scope)
                if value is not None:
                    scope[stmt.target.id] = value
    return scope


def _extract_schemas(tree: ast.AST, label: str = "<source>") -> list[str]:
    """Find every module-level or class-level ``SCHEMA`` string in ``tree``.

    Each ``SCHEMA`` is resolved in its own lexical scope (module constants,
    then the constants of the class it lives in), so same-named aliases in
    different classes stay independent. Function-local bindings are never used
    to resolve a class or module ``SCHEMA``.

    A ``SCHEMA`` that cannot be resolved to a static string is reported on
    stderr: the store it belongs to is not checked, and that must be visible
    rather than a silent skip.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _record(value_node: ast.AST, scope: dict[str, str]) -> None:
        value = _resolve(value_node, scope)
        if value is None:
            lineno = getattr(value_node, "lineno", "?")
            print(
                f"schema-column-guard: WARNING: {label}:{lineno}: SCHEMA is not a "
                f"resolvable string constant; this store is NOT checked by the guard",
                file=sys.stderr,
            )
            return
        if value not in seen:
            seen.add(value)
            out.append(value)

    def _scan(body: list[ast.stmt], scope: dict[str, str], in_function: bool) -> None:
        for stmt in body:
            if not in_function:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        if isinstance(target, ast.Name) and target.id == "SCHEMA":
                            _record(stmt.value, scope)
                elif isinstance(stmt, ast.AnnAssign):
                    if (
                        isinstance(stmt.target, ast.Name)
                        and stmt.target.id == "SCHEMA"
                        and stmt.value is not None
                    ):
                        _record(stmt.value, scope)

            if isinstance(stmt, ast.ClassDef):
                _scan(stmt.body, _scope_constants(stmt.body, scope), False)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Descend so a class nested in a function is still found, but
                # never let the function's own locals resolve a SCHEMA.
                _scan(stmt.body, scope, True)
            else:
                # Compound statements (if/try/with/for) at module or class
                # level still bind names in the enclosing scope.
                for _field, value in ast.iter_fields(stmt):
                    if (
                        isinstance(value, list)
                        and value
                        and all(isinstance(v, ast.stmt) for v in value)
                    ):
                        inner = scope if in_function else _scope_constants(value, scope)
                        _scan(value, inner, in_function)

    module_body = getattr(tree, "body", [])
    _scan(module_body, _scope_constants(module_body, {}), False)
    return out


def find_violations(path: Path, ref: str | None = None) -> list[Violation]:
    """Run the static check against a single Python file. Returns violations.

    Raises ``SourceError`` when the file cannot be read or parsed and
    ``RefError`` when its baseline cannot be read -- a file the guard cannot
    inspect is a hard failure, not a silent pass.
    """
    ref = ref or _baseline_ref()
    try:
        source = path.read_text(encoding="utf-8", errors="ignore")
    except OSError as exc:
        raise SourceError(
            f"schema-column-guard: ERROR: cannot read {path}: {exc}"
        ) from exc
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise SourceError(
            f"schema-column-guard: ERROR: cannot parse {path}: {exc}; "
            f"the guard cannot check this file"
        ) from exc

    schemas = _extract_schemas(tree, label=str(path))
    if not schemas:
        return []

    baseline_cols = _baseline_columns(path, ref)
    if baseline_cols is None:
        raise RefError(f"failed to read {ref} baseline for {path}")

    added_columns = _post_init_added_columns(tree)

    violations: list[Violation] = []
    for schema in schemas:
        for tm in _CREATE_TABLE_RE.finditer(schema):
            table = tm.group(1)
            if table in _EXCLUDED_TABLES:
                continue
            current_cols = _split_columns(tm.group(2))
            new_cols = current_cols - baseline_cols.get(table, set())
            for col in sorted(new_cols):
                if (table, col) not in added_columns:
                    violations.append(Violation(
                        path=path,
                        table=table,
                        column=col,
                        detail=f"new column '{col}' in CREATE TABLE {table} with no ALTER TABLE {table} ADD COLUMN {col} in this file",
                    ))
    return violations


def find_all_violations(
    root: Path = STORES_ROOT, ref: str | None = None
) -> tuple[list[Violation], bool]:
    """Walk every Python file under the stores root and collect violations.
    Returns (violations, had_error) where had_error is True if any file or
    baseline could not be read or parsed."""
    ref = ref or _baseline_ref()
    violations: list[Violation] = []
    had_error = False
    if not root.is_dir():
        return violations, had_error
    for py_file in sorted(root.rglob("*.py")):
        try:
            violations.extend(find_violations(py_file, ref))
        except GuardError as exc:
            print(str(exc), file=sys.stderr)
            had_error = True
    return violations, had_error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Flag SCHEMA columns added with no _post_init migration.",
    )
    parser.add_argument(
        "root",
        nargs="?",
        default=None,
        help="directory to scan (default: tinyagentos/)",
    )
    parser.add_argument(
        "--base",
        default=None,
        help=(
            "baseline ref new columns are measured against "
            f"(default: $BASE_REF qualified to origin/<branch>, else {DEFAULT_BASELINE_REF})"
        ),
    )
    ns = parser.parse_args(sys.argv[1:] if argv is None else argv)

    ref = _baseline_ref(ns.base)
    if not _check_base_ref(ref):
        print(
            f"schema-column-guard: ERROR: baseline ref {ref} is missing; "
            "this guard requires it to compare new columns. "
            f"Ensure {ref} is fetched and accessible, or pass --base.",
            file=sys.stderr,
        )
        return 2

    root = Path(ns.root) if ns.root else STORES_ROOT
    violations, had_error = find_all_violations(root, ref)
    # Print violations first so a single CI run shows the full picture even
    # when some other file could not be checked at all.
    for v in violations:
        print(f"SCHEMA-COLUMN VIOLATION: {v}")
    if had_error:
        return 2
    if not violations:
        print("schema-column-guard: clean")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
