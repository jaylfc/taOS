"""False-negative fixtures for check_retrofit_migrations (taOS #2188 / tsk-mhhgvn).

Each test exercises one defect class that the regex-only checker (origin/dev)
missed when scanning MIGRATIONS against a SCHEMA table:
  1. expression index  CREATE INDEX ... ON t (lower(name))
  2. quoted identifiers  ALTER TABLE "t" ADD COLUMN "name"
  3. unterminated CREATE TABLE in SCHEMA (regex requires trailing semicolon,
      so the table is dropped from schema_tables and retrofits on it are
      silently skipped)

The test asserts the fixed checker reports a violation.  On the unfixed
origin/dev checker these tests FAIL because the bad stores pass clean.
"""
from __future__ import annotations

import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_retrofit_migrations.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_retrofit_migrations", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


crm = _load_module()

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _write_tmp_store(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "synthetic_store.py"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Expression index in MIGRATIONS
# ---------------------------------------------------------------------------

def test_expression_index_retrofit(tmp_path: Path) -> None:
    body = textwrap.dedent(
        '''
        SCHEMA = """
        CREATE TABLE t (id INTEGER);
        """

        MIGRATIONS = [
            (1, "CREATE INDEX ix ON t (lower(name))"),
        ]
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = crm.find_violations(path)
    assert len(violations) >= 1, "expression CREATE INDEX on SCHEMA table must be flagged"
    assert any(v.table == "t" and v.kind == "CREATE INDEX" for v in violations)


# ---------------------------------------------------------------------------
# 2. Quoted identifiers in MIGRATIONS ALTER TABLE
# ---------------------------------------------------------------------------

def test_quoted_identifier_retrofit_alter(tmp_path: Path) -> None:
    body = textwrap.dedent(
        '''
        SCHEMA = """
        CREATE TABLE "t" (id INTEGER);
        """

        MIGRATIONS = [
            (1, 'ALTER TABLE "t" ADD COLUMN "name" TEXT'),
        ]
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = crm.find_violations(path)
    assert len(violations) >= 1, "quoted-identifier ALTER on SCHEMA table must be flagged"
    assert any(v.table == "t" and v.kind == "ALTER TABLE ADD COLUMN" for v in violations)


# ---------------------------------------------------------------------------
# 3. Unterminated CREATE TABLE in SCHEMA drops table from schema_tables
# ---------------------------------------------------------------------------

def test_unterminated_create_table_drops_schema_table(tmp_path: Path) -> None:
    body = textwrap.dedent(
        '''
        SCHEMA = """
        CREATE TABLE t (id INTEGER)
        """

        MIGRATIONS = [
            (1, "ALTER TABLE t ADD COLUMN name TEXT"),
        ]
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = crm.find_violations(path)
    assert len(violations) >= 1, (
        "ALTER on a table from an unterminated CREATE TABLE must still be flagged"
    )
    assert any(v.table == "t" and v.kind == "ALTER TABLE ADD COLUMN" for v in violations)


# ---------------------------------------------------------------------------
# main() exit-code integration
# ---------------------------------------------------------------------------

def test_main_exits_nonzero_on_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    body = textwrap.dedent(
        '''
        SCHEMA = """
        CREATE TABLE t (id INTEGER);
        """

        MIGRATIONS = [
            (1, "CREATE INDEX ix ON t (lower(name))"),
        ]
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    monkeypatch.setattr(crm, "STORES_ROOT", tmp_path)
    rc = crm.main([])
    assert rc == 1, "main() must exit 1 when violations exist"
