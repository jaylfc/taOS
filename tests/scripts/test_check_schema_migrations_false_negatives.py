"""False-negative fixtures for check_schema_migrations (taOS #1865 / tsk-mhhgvn).

Each test exercises one defect class that the regex-only checker (origin/dev)
missed:
  1. expression index  CREATE INDEX ... ON t (lower(name))
  2. quoted identifiers  ALTER TABLE "t" ADD COLUMN "name"
  3. unterminated CREATE TABLE (missing closing paren / semicolon)

The test asserts the fixed checker reports a violation.  On the unfixed
origin/dev checker these tests FAIL because the bad stores pass clean.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "check_schema_migrations.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_schema_migrations", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


csm = _load_module()

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _write_tmp_store(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "synthetic_store.py"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 1. Expression index  CREATE INDEX ix ON t (lower(name))
# ---------------------------------------------------------------------------

def test_expression_index_boot_brick(tmp_path: Path) -> None:
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
    assert len(violations) >= 1, "expression index on ALTER-added column must be flagged"
    assert any(v.table == "t" and v.column == "name" for v in violations)


# ---------------------------------------------------------------------------
# 2. Quoted identifiers in ALTER TABLE
# ---------------------------------------------------------------------------

def test_quoted_identifier_alter(tmp_path: Path) -> None:
    body = textwrap.dedent(
        '''
        from tinyagentos.base_store import BaseStore

        class QuotedStore(BaseStore):
            SCHEMA = """
            CREATE TABLE "t" (id INTEGER);
            CREATE INDEX ix ON "t" (name);
            """

            async def _post_init(self):
                await self._db.execute('ALTER TABLE "t" ADD COLUMN "name" TEXT')
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = csm.find_violations(path)
    assert len(violations) >= 1, "quoted-identifier ALTER on indexed column must be flagged"
    assert any(v.table == "t" and v.column == "name" for v in violations)


# ---------------------------------------------------------------------------
# 3. Unterminated CREATE TABLE (missing closing paren / semicolon)
# ---------------------------------------------------------------------------

def test_unterminated_create_table(tmp_path: Path) -> None:
    body = textwrap.dedent(
        '''
        from tinyagentos.base_store import BaseStore

        class UnterminatedStore(BaseStore):
            SCHEMA = """
            CREATE TABLE t (id INTEGER
            CREATE INDEX ix ON t (name);
            """

            async def _post_init(self):
                await self._db.execute("ALTER TABLE t ADD COLUMN name TEXT")
        '''
    )
    path = _write_tmp_store(tmp_path, body)
    violations = csm.find_violations(path)
    assert len(violations) >= 1, "index on ALTER-added column after unterminated CREATE TABLE must be flagged"
    assert any(v.table == "t" and v.column == "name" for v in violations)


# ---------------------------------------------------------------------------
# main() exit-code integration
# ---------------------------------------------------------------------------

def test_main_exits_nonzero_on_fixture(tmp_path: Path) -> None:
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
    violations = csm.find_all_violations(tmp_path)
    assert len(violations) >= 1
    assert csm.main([str(tmp_path)]) == 1, "main() must exit 1 when violations exist"
