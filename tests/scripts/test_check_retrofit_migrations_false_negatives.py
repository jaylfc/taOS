"""Tests for false negatives in the retrofit-migration-guard static check (taOS #2188).

Each test targets a defect class the regex-based checker missed:
1. Quoted identifiers in SCHEMA CREATE TABLE -- old ``\\w+`` cannot match
   ``"widgets"``, so the table is absent from ``schema_tables``.
2. Quoted identifiers in MIGRATIONS ALTER TABLE / CREATE INDEX -- old
   ``\\w+`` cannot match ``"widgets"``, so the migration is never flagged.
3. Case mismatch between SCHEMA table name and MIGRATIONS table name.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
import check_retrofit_migrations as crm  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_tmp_store(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "synthetic_store.py"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# False negative 1: quoted table name in SCHEMA CREATE TABLE
# ---------------------------------------------------------------------------

def test_quoted_schema_table_name_is_flagged(tmp_path: Path) -> None:
    body = textwrap.dedent(
        '''
        SCHEMA = """
        CREATE TABLE "widgets" (id INTEGER);
        """

        MIGRATIONS = [
            (1, 'ALTER TABLE "widgets" ADD COLUMN color TEXT'),
        ]
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = crm.find_violations(path)
    assert len(violations) == 1, f"expected 1 violation, got {len(violations)}: {violations}"
    v = violations[0]
    assert v.table == "widgets"
    assert v.kind == "ALTER TABLE ADD COLUMN"


# ---------------------------------------------------------------------------
# False negative 2: quoted table name in MIGRATIONS ALTER TABLE
# ---------------------------------------------------------------------------

def test_quoted_migration_table_name_is_flagged(tmp_path: Path) -> None:
    body = textwrap.dedent(
        '''
        SCHEMA = """
        CREATE TABLE widgets (id INTEGER);
        """

        MIGRATIONS = [
            (1, 'ALTER TABLE "widgets" ADD COLUMN color TEXT'),
        ]
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = crm.find_violations(path)
    assert len(violations) == 1, f"expected 1 violation, got {len(violations)}: {violations}"
    v = violations[0]
    assert v.table == "widgets"
    assert v.kind == "ALTER TABLE ADD COLUMN"


# ---------------------------------------------------------------------------
# False negative 3: case mismatch between SCHEMA table and MIGRATIONS table
# ---------------------------------------------------------------------------

def test_case_mismatch_schema_table_is_flagged(tmp_path: Path) -> None:
    body = textwrap.dedent(
        '''
        SCHEMA = """
        CREATE TABLE Widgets (id INTEGER);
        """

        MIGRATIONS = [
            (1, 'ALTER TABLE widgets ADD COLUMN color TEXT'),
        ]
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = crm.find_violations(path)
    assert len(violations) == 1, f"expected 1 violation, got {len(violations)}: {violations}"
    v = violations[0]
    assert v.table == "widgets"
    assert v.kind == "ALTER TABLE ADD COLUMN"
