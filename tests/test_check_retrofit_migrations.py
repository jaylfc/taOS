"""Tests for the retrofit-migration-guard static check (taOS #2188)."""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

# scripts/ is not a package; make it importable the same way the other
# scripts/*.py unit tests do (see tests/test_doc_gate.py).
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
import check_retrofit_migrations as crm  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_tmp_store(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "synthetic_store.py"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Real tree
# ---------------------------------------------------------------------------

def test_real_tree_is_clean() -> None:
    violations = crm.find_all_violations(REPO_ROOT / "tinyagentos")
    assert violations == [], [str(v) for v in violations]


# ---------------------------------------------------------------------------
# AnnAssign (annotated assignment) violations -- the gap the original script
# missed because it only handled ``ast.Assign``.
# ---------------------------------------------------------------------------

def test_annassign_alter_column_violation(tmp_path: Path) -> None:
    """``MIGRATIONS: list = [...]`` -- annotated assignment that adds a column
    to a SCHEMA table. The pre-fix script missed this entirely (returned
    clean instead of red). After the fix it is caught."""
    body = textwrap.dedent(
        '''
        SCHEMA = """
        CREATE TABLE widgets (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        """

        MIGRATIONS: list = [
            (1, "ALTER TABLE widgets ADD COLUMN color TEXT"),
        ]
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = crm.find_violations(path)
    assert len(violations) == 1
    v = violations[0]
    assert v.table == "widgets"
    assert v.kind == "ALTER TABLE ADD COLUMN"
    assert "retrofit" in v.detail.lower() or "add column" in v.detail.lower()


def test_annassign_create_index_violation(tmp_path: Path) -> None:
    """``MIGRATIONS: list = [...]`` -- annotated assignment that creates an
    index on a SCHEMA table. The fix text must be CREATE-INDEX appropriate,
    not the ADD COLUMN advice."""
    body = textwrap.dedent(
        '''
        SCHEMA = """
        CREATE TABLE widgets (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        """

        MIGRATIONS: list = [
            (1, "CREATE INDEX idx_widgets_name ON widgets(name)"),
        ]
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = crm.find_violations(path)
    assert len(violations) == 1
    v = violations[0]
    assert v.table == "widgets"
    assert v.kind == "CREATE INDEX"
    rendered = str(v)
    # CREATE INDEX fix text must mention index_list, NOT table_info / ALTER TABLE.
    assert "index_list" in rendered
    assert "CREATE INDEX IF NOT EXISTS" in rendered
    assert "table_info" not in rendered
    assert "ALTER TABLE only when absent" not in rendered


def test_annassign_class_level_violation(tmp_path: Path) -> None:
    """Annotated assignments inside a class body are also caught."""
    body = textwrap.dedent(
        '''
        from tinyagentos.base_store import BaseStore

        class BadStore(BaseStore):
            SCHEMA: str = """
            CREATE TABLE gadgets (
                id INTEGER PRIMARY KEY
            );
            """

            MIGRATIONS: list = [
                (1, "ALTER TABLE gadgets ADD COLUMN color TEXT"),
            ]
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = crm.find_violations(path)
    assert len(violations) == 1
    assert violations[0].store == "BadStore"
    assert violations[0].table == "gadgets"


def test_annassign_constant_reference_resolved(tmp_path: Path) -> None:
    """Annotated SCHEMA and MIGRATIONS that reference module-level constants
    are resolved before the check."""
    body = textwrap.dedent(
        '''
        SCHEMA_CONST = """
        CREATE TABLE widgets (
            id INTEGER PRIMARY KEY
        );
        """

        MIGRATIONS_CONST = [
            (1, "ALTER TABLE widgets ADD COLUMN color TEXT"),
        ]

        SCHEMA: str = SCHEMA_CONST
        MIGRATIONS: list = MIGRATIONS_CONST
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = crm.find_violations(path)
    assert len(violations) == 1
    assert violations[0].table == "widgets"


# ---------------------------------------------------------------------------
# Assign (plain assignment) violations -- the original pattern the script
# already handled, kept here as a regression check.
# ---------------------------------------------------------------------------

def test_assign_alter_column_violation(tmp_path: Path) -> None:
    """Plain ``MIGRATIONS = [...]`` (no annotation) is still caught."""
    body = textwrap.dedent(
        '''
        SCHEMA = """
        CREATE TABLE widgets (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        """

        MIGRATIONS = [
            (1, "ALTER TABLE widgets ADD COLUMN color TEXT"),
        ]
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = crm.find_violations(path)
    assert len(violations) == 1
    assert violations[0].table == "widgets"
    assert violations[0].kind == "ALTER TABLE ADD COLUMN"


def test_assign_create_index_violation(tmp_path: Path) -> None:
    """Plain ``MIGRATIONS = [...]`` with a CREATE INDEX on a SCHEMA table."""
    body = textwrap.dedent(
        '''
        SCHEMA = """
        CREATE TABLE widgets (
            id INTEGER PRIMARY KEY
        );
        """

        MIGRATIONS = [
            (1, "CREATE INDEX idx_w ON widgets(id)"),
        ]
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = crm.find_violations(path)
    assert len(violations) == 1
    assert violations[0].kind == "CREATE INDEX"


# ---------------------------------------------------------------------------
# Exemption cases
# ---------------------------------------------------------------------------

def test_exemption_bootstrap_schema_reference(tmp_path: Path) -> None:
    """``MIGRATIONS = [(1, SCHEMA)]`` -- the bootstrap pattern where the
    migration IS the schema.  Not a retrofit; not flagged."""
    body = textwrap.dedent(
        '''
        SCHEMA = """
        CREATE TABLE widgets (
            id INTEGER PRIMARY KEY,
            name TEXT
        );
        """

        MIGRATIONS: list = [
            (1, SCHEMA),
        ]
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    assert crm.find_violations(path) == []


def test_exemption_new_table_create(tmp_path: Path) -> None:
    """``CREATE TABLE IF NOT EXISTS`` for a table not in SCHEMA is a genuinely
    new table -- safe, not flagged."""
    body = textwrap.dedent(
        '''
        SCHEMA = """
        CREATE TABLE widgets (
            id INTEGER PRIMARY KEY
        );
        """

        MIGRATIONS: list = [
            (1, "CREATE TABLE IF NOT EXISTS audit_log (id INTEGER)"),
        ]
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    assert crm.find_violations(path) == []


# ---------------------------------------------------------------------------
# Unparseable file (SyntaxError) -- must be handled gracefully
# ---------------------------------------------------------------------------

def test_unparseable_file_returns_empty(tmp_path: Path) -> None:
    """A file with a SyntaxError is skipped (returns no violations) instead
    of crashing the whole run."""
    body = "SCHEMA = '''\nCREATE TABLE x (id INTEGER);\n'''  [[[\n"
    path = _write_tmp_store(tmp_path, body)
    assert crm.find_violations(path) == []


def test_unparseable_file_with_real_violation_returns_empty(tmp_path: Path) -> None:
    """Even if the file WOULD contain a violation, a SyntaxError means we
    cannot parse it, so we return empty (not crash)."""
    body = (
        "SCHEMA = '''\nCREATE TABLE x (id INTEGER);\n'''\n"
        "MIGRATIONS: list = [\n"
        "    (1, 'ALTER TABLE x ADD COLUMN y TEXT'),\n"
        "]]\n"
    )
    path = _write_tmp_store(tmp_path, body)
    assert crm.find_violations(path) == []


# ---------------------------------------------------------------------------
# main() exit code
# ---------------------------------------------------------------------------

def test_main_cleans_clean_when_no_violations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """main() prints the clean sentinel and exits 0 when the tree is clean."""
    monkeypatch.setattr(crm, "STORES_ROOT", tmp_path)
    clean_file = tmp_path / "ok_store.py"
    clean_file.write_text(
        'SCHEMA = """CREATE TABLE t (id INTEGER);"""\n'
        "MIGRATIONS = [(1, 'CREATE TABLE IF NOT EXISTS u (id INTEGER)')]\n",
        encoding="utf-8",
    )
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = crm.main([])
    assert rc == 0
    assert "retrofit-migration-guard: clean" in buf.getvalue()
