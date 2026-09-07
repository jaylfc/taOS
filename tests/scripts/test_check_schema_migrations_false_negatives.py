"""Tests for false negatives in the schema-migration-guard static check (taOS #1865).

Each test targets a defect class the regex-based checker missed:
1. Expression indexes (inner parens) -- old ``[^)]*`` stops at the first ``)``.
2. Quoted identifiers -- old ``\\w+`` cannot match ``"t"`` or ``"name"``.
3. Case mismatch between CREATE TABLE column and ALTER/INDEX column.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))
import check_schema_migrations as csm  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _write_tmp_store(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "synthetic_store.py"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# False negative 1: expression index ``ON t (lower(name))``
# ---------------------------------------------------------------------------

def test_expression_index_is_flagged(tmp_path: Path) -> None:
    body = textwrap.dedent(
        '''
        from tinyagentos.base_store import BaseStore

        class ExprIdxStore(BaseStore):
            SCHEMA = """
            CREATE TABLE t (id INTEGER);
            CREATE INDEX ix ON t (lower(name));
            """

            async def _post_init(self):
                await self._db.execute("ALTER TABLE t ADD COLUMN name TEXT")
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = csm.find_violations(path)
    assert len(violations) == 1, f"expected 1 violation, got {len(violations)}: {violations}"
    v = violations[0]
    assert v.table == "t"
    assert v.column == "name"


# ---------------------------------------------------------------------------
# False negative 2: quoted identifiers in CREATE INDEX and ALTER TABLE
# ---------------------------------------------------------------------------

def test_quoted_identifiers_are_flagged(tmp_path: Path) -> None:
    body = textwrap.dedent(
        '''
        from tinyagentos.base_store import BaseStore

        class QuotedStore(BaseStore):
            SCHEMA = """
            CREATE TABLE "t" ("id" INTEGER);
            CREATE INDEX ON "t" ("name");
            """

            async def _post_init(self):
                await self._db.execute('ALTER TABLE "t" ADD COLUMN "name" TEXT')
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = csm.find_violations(path)
    assert len(violations) == 1, f"expected 1 violation, got {len(violations)}: {violations}"
    v = violations[0]
    assert v.table == "t"
    assert v.column == "name"


# ---------------------------------------------------------------------------
# False negative 3: case mismatch between CREATE TABLE column and ALTER/INDEX
# ---------------------------------------------------------------------------

def test_case_mismatch_is_flagged(tmp_path: Path) -> None:
    body = textwrap.dedent(
        '''
        from tinyagentos.base_store import BaseStore

        class CaseStore(BaseStore):
            SCHEMA = """
            CREATE TABLE t (id INTEGER);
            CREATE INDEX ON t (Name);
            """

            async def _post_init(self):
                await self._db.execute("ALTER TABLE t ADD COLUMN name TEXT")
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = csm.find_violations(path)
    assert len(violations) == 1, f"expected 1 violation, got {len(violations)}: {violations}"
    v = violations[0]
    assert v.table == "t"
    assert v.column == "Name"
